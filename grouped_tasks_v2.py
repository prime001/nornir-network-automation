```python
"""
routing_snapshot.py - Network Routing Table Snapshot and Analysis

Purpose:
    Collect routing tables from network devices using Nornir, group results
    by device group, and produce a summary showing route counts by protocol
    (static, OSPF, BGP, EIGRP, connected) with optional prefix search.

Usage:
    python routing_snapshot.py
    python routing_snapshot.py --groups core,distribution --search 10.0.0.0/8
    python routing_snapshot.py --platform ios --vrf MGMT --output snapshot.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory files: hosts.yaml, groups.yaml, defaults.yaml
    SSH access to target devices with credentials in inventory or via CLI flags
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROTOCOL_CODES = {
    "S": "static",
    "O": "ospf",
    "B": "bgp",
    "D": "eigrp",
    "C": "connected",
    "L": "local",
    "i": "isis",
    "R": "rip",
}


def collect_routing_table(task: Task, vrf: str = "default") -> Result:
    cmd = "show ip route" if vrf == "default" else f"show ip route vrf {vrf}"
    result = task.run(task=netmiko_send_command, command_string=cmd)
    return Result(host=task.host, result=result.result)


def parse_route_counts(output: str) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Codes", "Gateway", "#")):
            continue
        proto = PROTOCOL_CODES.get(stripped[0])
        if proto:
            counts[proto] += 1
    return dict(counts)


def find_prefix_matches(output: str, prefix: str) -> List[str]:
    return [line.strip() for line in output.splitlines() if prefix in line]


def init_nornir(
    inventory: str,
    groups_file: str,
    defaults_file: str,
    username: Optional[str],
    password: Optional[str],
    groups_filter: Optional[List[str]],
    platform_filter: Optional[str],
) -> Nornir:
    nr = InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": inventory,
                "group_file": groups_file,
                "defaults_file": defaults_file,
            },
        },
        logging={"enabled": False},
    )
    if username:
        nr.inventory.defaults.username = username
    if password:
        nr.inventory.defaults.password = password
    if groups_filter:
        nr = nr.filter(lambda h: any(g in h.groups for g in groups_filter))
    if platform_filter:
        nr = nr.filter(platform=platform_filter)
    return nr


def print_summary(summary: Dict[str, dict], vrf: str, search: Optional[str]) -> None:
    print(f"\n{'='*65}")
    print(f"Routing Table Summary — VRF: {vrf}")
    print(f"{'='*65}")

    by_group: Dict[str, List[str]] = defaultdict(list)
    for hostname, data in summary.items():
        for grp in data.get("groups") or ["(ungrouped)"]:
            by_group[grp].append(hostname)

    header = f"  {'Host':<25} {'Total':>7}  {'Connected':>10}  {'OSPF':>6}  {'BGP':>5}  {'Static':>7}"
    divider = f"  {'-'*25} {'-'*7}  {'-'*10}  {'-'*6}  {'-'*5}  {'-'*7}"

    for group in sorted(by_group):
        print(f"\nGroup: {group}")
        print(header)
        print(divider)
        for hostname in by_group[group]:
            d = summary[hostname]
            rc = d["route_counts"]
            print(
                f"  {hostname:<25} {d['total_routes']:>7}"
                f"  {rc.get('connected', 0):>10}"
                f"  {rc.get('ospf', 0):>6}"
                f"  {rc.get('bgp', 0):>5}"
                f"  {rc.get('static', 0):>7}"
            )
            if search and "prefix_search" in d:
                ps = d["prefix_search"]
                status = "FOUND" if ps["found"] else "not found"
                print(f"    [{search}] {status}")
                for match in ps["matches"]:
                    print(f"      {match}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect and analyze routing tables across network devices"
    )
    parser.add_argument("--inventory", default="hosts.yaml")
    parser.add_argument("--groups-file", default="groups.yaml")
    parser.add_argument("--defaults-file", default="defaults.yaml")
    parser.add_argument("--username", help="Override inventory username")
    parser.add_argument("--password", help="Override inventory password")
    parser.add_argument("--groups", help="Comma-separated Nornir groups to target")
    parser.add_argument("--platform", help="Filter by platform (e.g. ios, eos)")
    parser.add_argument("--vrf", default="default", help="VRF to query")
    parser.add_argument("--search", metavar="PREFIX", help="Search for a prefix in results")
    parser.add_argument("--output", help="Write full results to JSON file")
    parser.add_argument("--verbose", action="store_true", help="Include raw output in JSON")
    args = parser.parse_args()

    groups_filter = [g.strip() for g in args.groups.split(",")] if args.groups else None

    try:
        nr = init_nornir(
            inventory=args.inventory,
            groups_file=args.groups_file,
            defaults_file=args.defaults_file,
            username=args.username,
            password=args.password,
            groups_filter=groups_filter,
            platform_filter=args.platform,
        )
    except Exception as exc:
        logger.error("Failed to initialize inventory: %s", exc)
        sys.exit(1)

    if not nr.inventory.hosts:
        logger.error("No hosts matched the specified filters.")
        sys.exit(1)

    logger.info("Targeting %d host(s)", len(nr.inventory.hosts))
    results = nr.run(task=collect_routing_table, vrf=args.vrf)

    summary: Dict[str, dict] = {}
    failed: List[str] = []

    for hostname, multi_result in results.items():
        if multi_result.failed:
            logger.warning("Collection failed for %s: %s", hostname, multi_result[0].exception)
            failed.append(hostname)
            continue

        raw = multi_result[0].result
        route_counts = parse_route_counts(raw)
        host_data: Dict = {
            "groups": [str(g) for g in nr.inventory.hosts[hostname].groups],
            "platform": nr.inventory.hosts[hostname].platform,
            "route_counts": route_counts,
            "total_routes": sum(route_counts.values()),
        }
        if args.search:
            matches = find_prefix_matches(raw, args.search)
            host_data["prefix_search"] = {"query": args.search, "matches": matches, "found": bool(matches)}
        if args.verbose:
            host_data["raw_output"] = raw
        summary[hostname] = host_data

    print_summary(summary, args.vrf, args.search)

    print(f"\n{'='*65}")
    print(f"Total: {len(summary)} succeeded, {len(failed)} failed")
    if failed:
        print(f"Failed hosts: {', '.join(failed)}")

    if args.output:
        try:
            with open(args.output, "w") as fh:
                json.dump(summary, fh, indent=2)
            logger.info("Results written to %s", args.output)
        except OSError as exc:
            logger.error("Could not write output file: %s", exc)
            sys.exit(1)


if __name__ == "__main__":
    main()
```