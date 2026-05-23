The existing scripts cover BGP, interfaces, VLANs, compliance, backups, threading, inventory, custom plugins, result filtering, and grouped tasks. Route table analysis is the clear gap — practical for NOC work, distinct from all existing scripts, and demonstrates NAPALM's `get_routes_to` alongside TextFSM parsing of raw `show ip route`.

```python
"""
route_table_analysis.py - IPv4 routing table collector and analyzer using Nornir.

Purpose:
    Connects to one or more network devices, retrieves the full IPv4 routing
    table (or a specific prefix lookup), and produces a structured report
    with route counts per protocol, next-hop distribution, and optional
    protocol/prefix filters.  Useful for rapid fleet-wide route audits,
    detecting unexpected redistributions, or verifying convergence after
    a network change.

Usage:
    # Single device
    python route_table_analysis.py --host 192.168.1.1 -u admin -p secret

    # Inventory file
    python route_table_analysis.py --inventory hosts.yaml --protocol ospf

    # Look up a specific prefix across all devices
    python route_table_analysis.py --inventory hosts.yaml --prefix 10.0.0.0/8

    # Show raw Nornir output in addition to the report
    python route_table_analysis.py --host 192.168.1.1 -u admin -p secret --verbose

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Devices must support 'show ip route' (IOS / IOS-XE / IOS-XR / NX-OS / EOS).
"""

import argparse
import logging
import sys
from collections import defaultdict
from typing import Optional

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_PROTO_CODES = {
    "C": "connected", "L": "local", "S": "static",
    "O": "ospf", "B": "bgp", "i": "isis",
    "R": "rip", "D": "eigrp", "E": "eigrp-ext",
    "N": "ospf-nssa", "IA": "ospf-ia",
}


def _parse_ios_routes(raw: str) -> list[dict]:
    routes = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] not in _PROTO_CODES:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        code = parts[0]
        prefix = next((p for p in parts if "/" in p or p.count(".") == 3), "")
        next_hop = ""
        for i, tok in enumerate(parts):
            if tok == "via" and i + 1 < len(parts):
                next_hop = parts[i + 1].rstrip(",")
                break
        routes.append({
            "protocol": _PROTO_CODES.get(code, code),
            "prefix": prefix,
            "next_hop": next_hop,
        })
    return routes


def collect_routes(task: Task, prefix_filter: Optional[str]) -> Result:
    cmd = f"show ip route {prefix_filter}" if prefix_filter else "show ip route"
    r = task.run(task=netmiko_send_command, command_string=cmd, use_textfsm=False)
    routes = _parse_ios_routes(r.result)
    task.host.data["routes"] = routes
    return Result(host=task.host, result=routes)


def _summarise(all_routes: dict) -> dict:
    summary = {}
    for host, routes in all_routes.items():
        by_proto: dict[str, int] = defaultdict(int)
        by_nh: dict[str, int] = defaultdict(int)
        for r in routes:
            by_proto[r["protocol"]] += 1
            if r["next_hop"]:
                by_nh[r["next_hop"]] += 1
        summary[host] = {
            "total": len(routes),
            "by_protocol": dict(sorted(by_proto.items())),
            "top_next_hops": sorted(by_nh.items(), key=lambda x: -x[1])[:5],
        }
    return summary


def _print_report(summary: dict, proto_filter: Optional[str]) -> None:
    print("\n=== Routing Table Analysis ===\n")
    for host, stats in summary.items():
        print(f"Host: {host}  (total routes: {stats['total']})")
        print("  Protocol breakdown:")
        for proto, count in stats["by_protocol"].items():
            if proto_filter and proto != proto_filter:
                continue
            print(f"    {proto:<14} {count:>5}")
        if stats["top_next_hops"]:
            print("  Top next-hops by route count:")
            for nh, count in stats["top_next_hops"]:
                print(f"    {nh:<22} {count:>4} routes")
        print()


def _build_nornir(args: argparse.Namespace) -> InitNornir:
    runner = {"plugin": "threaded", "options": {"num_workers": args.workers}}
    if args.inventory:
        return InitNornir(
            runner=runner,
            inventory={
                "plugin": "SimpleInventory",
                "options": {"host_file": args.inventory},
            },
        )
    hosts = Hosts({
        args.host: Host(
            name=args.host,
            hostname=args.host,
            username=args.username,
            password=args.password,
            platform=args.platform,
            port=args.port,
        )
    })
    inventory = Inventory(hosts=hosts, groups=Groups(), defaults=Defaults())
    return InitNornir(runner=runner, inventory=inventory)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect and analyze IPv4 routing tables across network devices."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", metavar="HOST", help="Single device hostname or IP")
    target.add_argument("--inventory", metavar="FILE", help="Nornir SimpleInventory hosts YAML")
    parser.add_argument("-u", "--username", default="admin")
    parser.add_argument("-p", "--password", default="")
    parser.add_argument("--platform", default="cisco_ios",
                        help="Netmiko platform string (default: cisco_ios)")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--prefix", metavar="PREFIX",
                        help="Look up a specific prefix, e.g. 10.0.0.0/8")
    parser.add_argument("--protocol", metavar="PROTO",
                        help="Filter report by protocol name (ospf, bgp, static, ...)")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--verbose", action="store_true",
                        help="Print raw Nornir task results before the report")
    args = parser.parse_args()

    nr = _build_nornir(args)
    logger.warning("Targeting %d host(s)", len(nr.inventory.hosts))

    results = nr.run(task=collect_routes, prefix_filter=args.prefix, name="collect_routes")

    if args.verbose:
        print_result(results)

    failed = [h for h, r in results.items() if r.failed]
    if failed:
        print(f"[WARN] Failed to connect: {', '.join(failed)}", file=sys.stderr)

    collected = {h: r.result for h, r in results.items() if not r.failed and r.result is not None}
    if not collected:
        print("No route data collected from any device.", file=sys.stderr)
        return 1

    summary = _summarise(collected)
    _print_report(summary, args.protocol)
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
```