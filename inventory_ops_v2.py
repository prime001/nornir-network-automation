The output format requires only script content. Writing the script directly to stdout now.

"""
inventory_health.py — Nornir Inventory Health Check

Purpose:
    Validate that every device in a Nornir inventory is reachable over SSH,
    that credentials authenticate successfully, and produce a per-group
    reachability summary suitable for ops dashboards or CI gates.

    Distinguishes three failure modes:
      UNREACHABLE  — TCP/SSH handshake failed (device down, firewall, wrong IP)
      AUTH_FAIL    — SSH connected but credentials were rejected
      TIMEOUT      — Connection attempt exceeded --timeout seconds

Usage:
    python inventory_health.py
    python inventory_health.py --filter-group core-routers --timeout 10
    python inventory_health.py --filter-host rtr-nyc-01 --verbose
    python inventory_health.py --username admin --password secret --workers 20

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: inventory/hosts.yaml, inventory/groups.yaml,
                     inventory/defaults.yaml
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _classify_error(message: str) -> str:
    msg = message.lower()
    if any(k in msg for k in ("authentication", "permission denied", "auth failed", "no authentication")):
        return "AUTH_FAIL"
    if any(k in msg for k in ("timed out", "timeout", "connection timeout")):
        return "TIMEOUT"
    return "UNREACHABLE"


def check_device(task: Task, cmd_timeout: int) -> Result:
    """Run a lightweight command to validate connectivity and auth."""
    try:
        task.run(
            task=netmiko_send_command,
            command_string="show version",
            read_timeout=cmd_timeout,
        )
        return Result(
            host=task.host,
            result={"status": "OK", "platform": task.host.platform or "unknown"},
        )
    except Exception as exc:
        error_str = str(exc)
        status = _classify_error(error_str)
        return Result(
            host=task.host,
            result={
                "status": status,
                "error": error_str[:140],
                "platform": task.host.platform or "unknown",
            },
            failed=True,
        )


def build_report(nr_results) -> dict:
    totals = {"OK": 0, "AUTH_FAIL": 0, "TIMEOUT": 0, "UNREACHABLE": 0}
    by_group: dict = defaultdict(lambda: defaultdict(int))
    failures = []

    for host_name, multi in nr_results.items():
        inner = multi[0]
        data = inner.result if isinstance(inner.result, dict) else {}
        status = data.get("status", "UNREACHABLE")
        host_groups = [str(g) for g in multi[0].host.groups] or ["ungrouped"]
        primary_group = host_groups[0]

        totals[status] = totals.get(status, 0) + 1
        by_group[primary_group][status] += 1

        if status != "OK":
            failures.append({
                "host": host_name,
                "group": primary_group,
                "status": status,
                "error": data.get("error", ""),
            })

    return {"totals": totals, "by_group": by_group, "failures": failures}


def print_report(report: dict) -> None:
    t = report["totals"]
    total_devices = sum(t.values())
    healthy = t.get("AUTH_FAIL", 0) == 0 and t.get("TIMEOUT", 0) == 0 and t.get("UNREACHABLE", 0) == 0

    print("\n" + "=" * 62)
    print("  INVENTORY HEALTH REPORT")
    print("=" * 62)
    print(f"  Total    : {total_devices}")
    print(f"  OK       : {t.get('OK', 0)}")
    print(f"  Auth fail: {t.get('AUTH_FAIL', 0)}")
    print(f"  Timeout  : {t.get('TIMEOUT', 0)}")
    print(f"  Unreachbl: {t.get('UNREACHABLE', 0)}")

    if report["by_group"]:
        print("\n  By Group:")
        for group, counts in sorted(report["by_group"].items()):
            group_total = sum(counts.values())
            ok_count = counts.get("OK", 0)
            bar = "OK" if ok_count == group_total else "DEGRADED"
            print(f"    {group:<28} {ok_count}/{group_total}  [{bar}]")

    if report["failures"]:
        print("\n  Failures:")
        for f in report["failures"]:
            print(f"    [{f['status']:<11}] {f['host']}  ({f['group']})")
            if f["error"]:
                print(f"               {f['error']}")

    print("=" * 62)
    print(f"  Overall: {'HEALTHY' if healthy else 'DEGRADED'}")
    print("=" * 62 + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validate Nornir inventory reachability and credentials",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--hosts-file", default="inventory/hosts.yaml")
    p.add_argument("--groups-file", default="inventory/groups.yaml")
    p.add_argument("--defaults-file", default="inventory/defaults.yaml")
    p.add_argument("--filter-group", metavar="GROUP", help="Limit check to one group")
    p.add_argument("--filter-host", metavar="HOST", help="Limit check to one host")
    p.add_argument("--timeout", type=int, default=15, help="SSH timeout in seconds")
    p.add_argument("--workers", type=int, default=10, help="Parallel worker threads")
    p.add_argument("--username", help="Override inventory username")
    p.add_argument("--password", help="Override inventory password")
    p.add_argument("--verbose", action="store_true", help="Print raw Nornir output")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.filter_group and args.filter_host:
        logger.error("Specify --filter-group or --filter-host, not both")
        return 1

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.hosts_file,
                "group_file": args.groups_file,
                "defaults_file": args.defaults_file,
            },
        },
        logging={"enabled": False},
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if args.filter_group:
        nr = nr.filter(F(groups__contains=args.filter_group))
    elif args.filter_host:
        nr = nr.filter(name=args.filter_host)

    if not nr.inventory.hosts:
        logger.error("No hosts matched the specified filter")
        return 1

    logger.info(
        "Checking %d host(s) — workers=%d  timeout=%ds",
        len(nr.inventory.hosts),
        args.workers,
        args.timeout,
    )

    results = nr.run(
        task=check_device,
        cmd_timeout=args.timeout,
        name="inventory_health",
    )

    if args.verbose:
        print_result(results)

    report = build_report(results)
    print_report(report)

    t = report["totals"]
    degraded = t.get("AUTH_FAIL", 0) + t.get("TIMEOUT", 0) + t.get("UNREACHABLE", 0) > 0
    return 1 if degraded else 0


if __name__ == "__main__":
    sys.exit(main())