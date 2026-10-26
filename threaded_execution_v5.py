Route Table Analyzer - Nornir Network Automation

Purpose:
    Collect and analyze IP routing tables across multiple network devices
    concurrently. Reports route counts by protocol, flags devices missing
    a default route, and optionally searches for a specific prefix across
    the fleet — useful for verifying route propagation after changes.

Usage:
    python route_table_analyzer.py [--hosts R1 R2] [--groups core]
                                   [--protocol {ospf,bgp,static,connected,all}]
                                   [--search-prefix 10.0.0.0/8]
                                   [--workers 10] [--verbose]

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: hosts.yaml, groups.yaml, defaults.yaml
    Devices must support 'show ip route' (Cisco IOS/IOS-XE/NX-OS).
"""

import argparse
import logging
import sys
from collections import defaultdict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logger = logging.getLogger(__name__)

PROTO_PREFIXES = {
    "ospf": ("O ", "O E", "O N", "O I"),
    "bgp": ("B ",),
    "static": ("S ",),
    "connected": ("C ",),
    "eigrp": ("D ", "EX "),
}

PROTO_CMD = {
    "ospf": "ospf",
    "bgp": "bgp",
    "static": "static",
    "connected": "connected",
    "eigrp": "eigrp",
}


def _parse_routes(output: str, search_prefix: str) -> dict:
    counts = defaultdict(int)
    has_default = False
    matched = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ("#", "!") or "odes:" in stripped:
            continue
        if "0.0.0.0/0" in stripped or "0.0.0.0 0.0.0.0" in stripped:
            has_default = True
        for proto, prefixes in PROTO_PREFIXES.items():
            if any(stripped.startswith(p) for p in prefixes):
                counts[proto] += 1
                break
        if search_prefix and search_prefix in stripped:
            matched.append(stripped)

    return {
        "total": sum(counts.values()),
        "by_proto": dict(counts),
        "has_default": has_default,
        "matched": matched,
    }


def collect_routes(task: Task, protocol: str, search_prefix: str) -> Result:
    cmd = "show ip route"
    if protocol != "all":
        cmd = f"show ip route {PROTO_CMD[protocol]}"

    r = task.run(task=netmiko_send_command, command_string=cmd, name="show ip route")
    parsed = _parse_routes(r.result, search_prefix)
    return Result(host=task.host, result=parsed)


def _print_summary(data: dict, search_prefix: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"{'ROUTE TABLE ANALYSIS':^{width}}")
    print("=" * width)
    print(f"{'Host':<22} {'Total':>6} {'OSPF':>6} {'BGP':>6} {'Static':>7} {'Default':>8}")
    print("-" * width)

    for host in sorted(data):
        entry = data[host]
        if "error" in entry:
            print(f"{host:<22}  ERROR: {entry['error']}")
            continue
        bp = entry.get("by_proto", {})
        default_flag = "YES" if entry.get("has_default") else "NO "
        print(
            f"{host:<22} {entry.get('total', 0):>6} "
            f"{bp.get('ospf', 0):>6} {bp.get('bgp', 0):>6} "
            f"{bp.get('static', 0):>7} {default_flag:>8}"
        )

    if search_prefix:
        print(f"\n--- Prefix search: {search_prefix} ---")
        for host in sorted(data):
            entry = data[host]
            if "error" in entry:
                continue
            lines = entry.get("matched", [])
            if lines:
                print(f"  {host}: FOUND ({len(lines)} line(s))")
                for ln in lines[:3]:
                    print(f"    {ln}")
            else:
                print(f"  {host}: not found")

    print("=" * width)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect and analyze IP routing tables across devices with Nornir"
    )
    p.add_argument("--hosts", nargs="+", metavar="HOST", help="Filter to specific hostnames")
    p.add_argument("--groups", nargs="+", metavar="GROUP", help="Filter to inventory groups")
    p.add_argument(
        "--protocol",
        choices=["ospf", "bgp", "static", "connected", "eigrp", "all"],
        default="all",
        help="Collect only this protocol's routes (default: all)",
    )
    p.add_argument(
        "--search-prefix", metavar="PREFIX",
        help="Search for a specific prefix across all route tables (e.g. 192.168.1.0/24)",
    )
    p.add_argument("--workers", type=int, default=10, help="Concurrent threads (default: 10)")
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--verbose", action="store_true", help="Print raw Nornir task output")
    p.add_argument("--debug", action="store_true", help="Enable debug-level logging")
    return p


def main() -> int:
    args = _build_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        nr = InitNornir(
            config_file=args.config,
            runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        )
    except Exception as exc:
        print(f"ERROR: Failed to load Nornir config '{args.config}': {exc}", file=sys.stderr)
        return 1

    if args.hosts:
        nr = nr.filter(F(name__any=args.hosts))
    if args.groups:
        nr = nr.filter(F(groups__any=args.groups))

    if not nr.inventory.hosts:
        print("ERROR: No hosts matched the given filters.", file=sys.stderr)
        return 1

    host_count = len(nr.inventory.hosts)
    print(
        f"Analyzing routes on {host_count} device(s) "
        f"[protocol={args.protocol}, workers={args.workers}]..."
    )

    results = nr.run(
        task=collect_routes,
        protocol=args.protocol,
        search_prefix=args.search_prefix or "",
        name="Route Table Analysis",
    )

    if args.verbose:
        print_result(results)

    aggregated = {}
    failed = 0
    for host, multi in results.items():
        if multi.failed:
            exc = multi[0].exception
            aggregated[host] = {"error": str(exc) if exc else "task failed"}
            failed += 1
        else:
            aggregated[host] = multi[0].result

    _print_summary(aggregated, args.search_prefix or "")

    if failed:
        print(f"\nWARNING: {failed}/{host_count} device(s) failed.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())