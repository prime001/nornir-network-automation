```python
"""
route_lookup.py - Network-wide route table lookup and analysis using Nornir.

Purpose:
    Query routing tables across multiple network devices simultaneously, filter
    by protocol or specific prefix, and produce a structured cross-device report.
    Useful for validating route propagation, auditing reachability, and
    troubleshooting forwarding issues across a multi-device fleet.

Usage:
    python route_lookup.py --hosts inventory/hosts.yaml \\
        --username admin --password secret \\
        [--protocol ospf|bgp|static|connected|eigrp|all] \\
        [--prefix 10.0.0.0/24] [--filter-group core_routers] \\
        [--output routes.json] [--workers 10]

Prerequisites:
    pip install nornir nornir-netmiko netmiko
    Nornir inventory files with Cisco IOS/IOS-XE or compatible devices.
    NTC TextFSM templates installed (via ntc-templates) for structured parsing.
"""

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.task import MultiResult, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("route_lookup")

PROTOCOL_COMMANDS: Dict[str, str] = {
    "all": "show ip route",
    "ospf": "show ip route ospf",
    "bgp": "show ip route bgp",
    "static": "show ip route static",
    "connected": "show ip route connected",
    "eigrp": "show ip route eigrp",
}


def task_fetch_routes(task: Task, protocol: str, prefix: Optional[str]) -> Any:
    cmd = PROTOCOL_COMMANDS.get(protocol, PROTOCOL_COMMANDS["all"])
    if prefix:
        cmd = f"show ip route {prefix}"

    r = task.run(
        task=netmiko_send_command,
        command_string=cmd,
        use_textfsm=True,
    )
    return r.result


def build_nornir(args: argparse.Namespace) -> Nornir:
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.hosts,
                "group_file": args.groups,
                "defaults_file": args.defaults,
            },
        },
        logging={"enabled": False},
    )
    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password
    if args.filter_group:
        nr = nr.filter(groups__contains=args.filter_group)
    return nr


def parse_entries(raw: Any) -> List[Dict]:
    if isinstance(raw, list):
        return raw
    return [{"raw_output": str(raw)}]


def build_report(all_results: MultiResult, prefix: Optional[str]) -> Dict:
    report: Dict[str, Any] = {}
    for hostname, multi_result in all_results.items():
        if multi_result.failed:
            logger.warning("%-20s FAILED: %s", hostname, multi_result.exception)
            report[hostname] = {
                "status": "failed",
                "error": str(multi_result.exception),
            }
            continue

        entries = parse_entries(multi_result[0].result)

        if prefix and entries and "raw_output" not in entries[0]:
            net = prefix.split("/")[0]
            entries = [e for e in entries if net in e.get("network", "")]

        report[hostname] = {
            "status": "ok",
            "route_count": len(entries),
            "routes": entries,
        }
        logger.info("%-20s OK — %d route(s)", hostname, len(entries))

    return report


def print_summary(report: Dict) -> None:
    ok = [h for h, d in report.items() if d["status"] == "ok"]
    failed = [h for h, d in report.items() if d["status"] == "failed"]
    total_routes = sum(report[h]["route_count"] for h in ok)

    width = 55
    print(f"\n{'=' * width}")
    print("  Route Lookup Summary")
    print(f"{'=' * width}")
    print(f"  Devices queried  : {len(report)}")
    print(f"  Successful       : {len(ok)}")
    print(f"  Failed           : {len(failed)}")
    print(f"  Total routes     : {total_routes}")
    if ok:
        per_device = [(h, report[h]["route_count"]) for h in ok]
        per_device.sort(key=lambda x: x[1], reverse=True)
        print(f"\n  Routes per device:")
        for host, count in per_device:
            print(f"    {host:<30} {count:>5}")
    if failed:
        print(f"\n  Failed hosts: {', '.join(failed)}")
    print(f"{'=' * width}\n")


def main(args: argparse.Namespace) -> int:
    if args.protocol not in PROTOCOL_COMMANDS:
        logger.error(
            "Unknown protocol '%s'. Choices: %s",
            args.protocol,
            ", ".join(PROTOCOL_COMMANDS),
        )
        return 1

    logger.info(
        "Route lookup — protocol=%s prefix=%s workers=%d",
        args.protocol,
        args.prefix or "all",
        args.workers,
    )

    try:
        nr = build_nornir(args)
    except Exception as exc:
        logger.error("Inventory initialization failed: %s", exc)
        return 1

    if not nr.inventory.hosts:
        logger.error("No hosts in inventory (filter may be too restrictive).")
        return 1

    logger.info("Querying %d host(s)...", len(nr.inventory.hosts))

    all_results = nr.run(
        task=task_fetch_routes,
        name="fetch_routes",
        protocol=args.protocol,
        prefix=args.prefix,
    )

    report = build_report(all_results, args.prefix)
    print_summary(report)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, default=str)
            logger.info("JSON report written to %s", args.output)
        except OSError as exc:
            logger.error("Failed to write report: %s", exc)
            return 1

    return 1 if any(d["status"] == "failed" for d in report.values()) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Query and report routing tables across a Nornir device fleet.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--hosts", default="inventory/hosts.yaml",
        help="Path to Nornir hosts inventory YAML",
    )
    parser.add_argument(
        "--groups", default="inventory/groups.yaml",
        help="Path to Nornir groups inventory YAML",
    )
    parser.add_argument(
        "--defaults", default="inventory/defaults.yaml",
        help="Path to Nornir defaults inventory YAML",
    )
    parser.add_argument("--username", help="SSH username (overrides inventory)")
    parser.add_argument("--password", help="SSH password (overrides inventory)")
    parser.add_argument(
        "--protocol",
        default="all",
        choices=list(PROTOCOL_COMMANDS.keys()),
        help="Routing protocol to filter",
    )
    parser.add_argument(
        "--prefix",
        metavar="A.B.C.D[/LEN]",
        help="Look up a specific prefix (e.g. 192.168.10.0/24)",
    )
    parser.add_argument(
        "--filter-group",
        metavar="GROUP",
        help="Restrict run to hosts belonging to this Nornir group",
    )
    parser.add_argument(
        "--output",
        metavar="FILE.json",
        help="Write full JSON report to this path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Concurrent Nornir worker threads",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )

    parsed = parser.parse_args()
    if parsed.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    sys.exit(main(parsed))
```