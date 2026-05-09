route_filter.py — Nornir Route Table Filter
============================================
Query routing tables from one or more network devices and filter the results
by routing protocol, prefix string, or next-hop address.

Usage:
    python route_filter.py --protocol ospf
    python route_filter.py --prefix 10.0.0 --filter-group core-routers
    python route_filter.py --next-hop 192.168.1.1

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
    Devices must accept 'show ip route' (Cisco IOS / IOS-XE / NX-OS compatible).
"""

import argparse
import logging
import re
import sys
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

log = logging.getLogger(__name__)

PROTOCOL_MAP = {
    "ospf": r"^O",
    "bgp": r"^B",
    "static": r"^S",
    "connected": r"^C",
    "eigrp": r"^D",
    "rip": r"^R",
    "isis": r"^i",
}


def parse_routes(raw: str) -> List[Dict[str, str]]:
    """Parse 'show ip route' text into a list of route dicts."""
    routes: List[Dict[str, str]] = []
    proto_code = ""
    current_prefix = ""

    for line in raw.splitlines():
        # Primary route line: protocol code + prefix + metric
        m = re.match(
            r"^([A-Z][A-Z0-9 *]*?)\s+([\d./]+(?:/\d+)?)\s+\[", line
        )
        if m:
            proto_code = m.group(1).strip()
            current_prefix = m.group(2).strip()
            nh = re.search(r"via\s+([\d.]+)", line)
            iface = re.search(r"via\s+[\d.]+,\s+(\S+)", line)
            routes.append(
                {
                    "prefix": current_prefix,
                    "protocol": proto_code,
                    "next_hop": nh.group(1) if nh else "directly connected",
                    "interface": iface.group(1).rstrip(",") if iface else "",
                }
            )
        elif current_prefix and re.match(r"^\s+\[", line):
            # ECMP continuation line for the same prefix
            nh = re.search(r"via\s+([\d.]+)", line)
            iface = re.search(r"via\s+[\d.]+,\s+(\S+)", line)
            routes.append(
                {
                    "prefix": current_prefix,
                    "protocol": proto_code,
                    "next_hop": nh.group(1) if nh else "directly connected",
                    "interface": iface.group(1).rstrip(",") if iface else "",
                }
            )

    return routes


def apply_filters(
    routes: List[Dict[str, str]],
    protocol: Optional[str],
    prefix: Optional[str],
    next_hop: Optional[str],
) -> List[Dict[str, str]]:
    result = routes
    if protocol:
        pattern = PROTOCOL_MAP.get(protocol.lower(), protocol)
        result = [r for r in result if re.match(pattern, r["protocol"], re.IGNORECASE)]
    if prefix:
        result = [r for r in result if prefix in r["prefix"]]
    if next_hop:
        result = [r for r in result if next_hop in r["next_hop"]]
    return result


def fetch_routes(task: Task) -> Result:
    """Nornir task: run 'show ip route' and return raw output."""
    r = task.run(
        task=netmiko_send_command,
        command_string="show ip route",
        use_textfsm=False,
    )
    return Result(host=task.host, result=r.result)


def print_table(hostname: str, routes: List[Dict[str, str]]) -> None:
    if not routes:
        print(f"  {hostname}: no matching routes\n")
        return
    print(f"  {hostname}  ({len(routes)} route{'s' if len(routes) != 1 else ''})")
    print(f"  {'PREFIX':<22} {'PROTOCOL':<12} {'NEXT-HOP':<20} INTERFACE")
    print("  " + "-" * 70)
    for r in routes:
        print(
            f"  {r['prefix']:<22} {r['protocol']:<12} {r['next_hop']:<20} {r['interface']}"
        )
    print()


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Filter routing tables across network devices with Nornir",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--inventory", default="hosts.yaml", metavar="FILE")
    p.add_argument("--groups-file", default="groups.yaml", metavar="FILE")
    p.add_argument("--defaults-file", default="defaults.yaml", metavar="FILE")
    p.add_argument("--filter-group", metavar="GROUP", help="Limit to a Nornir host group")
    p.add_argument(
        "--protocol",
        choices=list(PROTOCOL_MAP.keys()),
        metavar="PROTO",
        help=f"Routing protocol: {', '.join(PROTOCOL_MAP)}",
    )
    p.add_argument("--prefix", metavar="STR", help="Substring match on prefix (e.g. '10.0.0')")
    p.add_argument("--next-hop", metavar="IP", help="Exact or partial next-hop IP match")
    p.add_argument("--workers", type=int, default=10, help="Parallel thread count")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> None:
    args = build_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    if not any([args.protocol, args.prefix, args.next_hop]):
        log.warning("No filter specified — all routes will be shown")

    try:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
            inventory={
                "plugin": "SimpleInventory",
                "options": {
                    "host_file": args.inventory,
                    "group_file": args.groups_file,
                    "defaults_file": args.defaults_file,
                },
            },
        )
    except Exception as exc:
        log.error("Nornir init failed: %s", exc)
        sys.exit(1)

    if args.filter_group:
        nr = nr.filter(F(groups__contains=args.filter_group))
        if not nr.inventory.hosts:
            log.error("No hosts found in group '%s'", args.filter_group)
            sys.exit(1)

    log.info("Querying %d device(s)", len(nr.inventory.hosts))
    agg = nr.run(task=fetch_routes, name="fetch_routes")

    failed = [h for h, r in agg.items() if r.failed]
    if failed:
        log.warning("Unreachable: %s", ", ".join(failed))

    print("\nRoute Filter Results")
    print("=" * 72)
    total = 0
    for hostname, multi in agg.items():
        if multi.failed:
            print(f"  {hostname}: ERROR — {multi[0].exception}\n")
            continue
        routes = parse_routes(multi[0].result)
        filtered = apply_filters(routes, args.protocol, args.prefix, args.next_hop)
        print_table(hostname, filtered)
        total += len(filtered)

    print(f"Total matching routes: {total}  |  Devices queried: {len(agg)}"
          f"  |  Failures: {len(failed)}")


if __name__ == "__main__":
    main()