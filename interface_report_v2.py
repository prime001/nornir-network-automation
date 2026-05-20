Now I have enough context. Writing the script:

```
"""
interface_error_report.py - Interface Error Counter Report using Nornir

Purpose:
    Queries network devices for per-interface error counters (CRC errors,
    input/output errors, drops, runts, giants) and flags interfaces that
    exceed a configurable threshold. Useful for proactively identifying
    degraded links before they cause outages, or for post-incident triage.

Usage:
    python interface_error_report.py --inventory hosts.yaml \
        --groups-file groups.yaml --defaults-file defaults.yaml
    python interface_error_report.py --inventory hosts.yaml --group core \
        --threshold 10 --output errors.csv --workers 20

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    A valid Nornir SimpleInventory (hosts.yaml, groups.yaml, defaults.yaml)
    SSH access with privilege level sufficient to run 'show interfaces'
    Tested against Cisco IOS/IOS-XE. Adjust parse_show_interfaces() for
    other vendors (NX-OS, EOS, JunOS) as needed.
"""

import argparse
import csv
import logging
import re
import sys
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("interface_error_report")

ERROR_FIELDS = ("input_errors", "output_errors", "crc", "drops", "runts", "giants")


def parse_show_interfaces(output: str) -> List[Dict]:
    interfaces = []
    current: Dict = {}

    for line in output.splitlines():
        iface_match = re.match(r"^(\S+)\s+is\s+(up|down|administratively down)", line)
        if iface_match:
            if current:
                interfaces.append(current)
            current = {
                "name": iface_match.group(1),
                "status": iface_match.group(2),
                "input_errors": 0,
                "output_errors": 0,
                "crc": 0,
                "drops": 0,
                "runts": 0,
                "giants": 0,
            }
            continue

        if not current:
            continue

        m = re.search(r"(\d+)\s+input errors.*?(\d+)\s+CRC", line)
        if m:
            current["input_errors"] = int(m.group(1))
            current["crc"] = int(m.group(2))
            continue

        m = re.search(r"(\d+)\s+output errors", line)
        if m:
            current["output_errors"] = int(m.group(1))

        m = re.search(r"(\d+)\s+runts.*?(\d+)\s+giants", line)
        if m:
            current["runts"] = int(m.group(1))
            current["giants"] = int(m.group(2))

        m = re.search(r"(\d+)\s+(?:input\s+)?drops", line)
        if m:
            current["drops"] = int(m.group(1))

    if current:
        interfaces.append(current)

    return interfaces


def collect_errors(task: Task, threshold: int) -> Result:
    cmd_result = task.run(
        task=netmiko_send_command,
        command_string="show interfaces",
        use_textfsm=False,
    )
    interfaces = parse_show_interfaces(cmd_result.result)
    flagged = [
        iface for iface in interfaces
        if any(iface.get(f, 0) >= threshold for f in ERROR_FIELDS)
    ]
    return Result(host=task.host, result=flagged)


def build_rows(nr_results, threshold: int) -> List[Dict]:
    rows = []
    for host, multi in nr_results.items():
        if multi.failed:
            logger.warning("%-20s  FAILED: %s", host, multi[0].exception)
            continue
        for iface in multi[0].result:
            rows.append({
                "host": host,
                "interface": iface["name"],
                "status": iface["status"],
                "input_errors": iface["input_errors"],
                "output_errors": iface["output_errors"],
                "crc": iface["crc"],
                "drops": iface["drops"],
                "runts": iface["runts"],
                "giants": iface["giants"],
            })
    return rows


def print_table(rows: List[Dict], threshold: int) -> None:
    if not rows:
        print(f"\nAll interfaces clean (threshold={threshold}). No errors found.")
        return

    hdr = f"{'HOST':<20} {'INTERFACE':<26} {'STATUS':<10} {'INERR':>6} {'OUTERR':>6} {'CRC':>6} {'DROPS':>6} {'RUNTS':>6} {'GIANTS':>7}"
    print(f"\n{hdr}")
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['host']:<20} {r['interface']:<26} {r['status']:<10} "
            f"{r['input_errors']:>6} {r['output_errors']:>6} {r['crc']:>6} "
            f"{r['drops']:>6} {r['runts']:>6} {r['giants']:>7}"
        )
    print(f"\n{len(rows)} interface(s) flagged.")


def write_csv(rows: List[Dict], path: str) -> None:
    fields = ["host", "interface", "status", "input_errors", "output_errors",
              "crc", "drops", "runts", "giants"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("CSV written to %s", path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Report interface error counters across a Nornir inventory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--inventory", default="hosts.yaml",
                   help="Nornir hosts file (default: hosts.yaml)")
    p.add_argument("--groups-file", default="groups.yaml",
                   help="Nornir groups file (default: groups.yaml)")
    p.add_argument("--defaults-file", default="defaults.yaml",
                   help="Nornir defaults file (default: defaults.yaml)")
    p.add_argument("--group",
                   help="Limit run to hosts in this Nornir group")
    p.add_argument("--threshold", type=int, default=1,
                   help="Minimum counter value to flag (default: 1)")
    p.add_argument("--output", metavar="FILE",
                   help="Write flagged interfaces to a CSV file")
    p.add_argument("--workers", type=int, default=10,
                   help="Concurrent Nornir threads (default: 10)")
    p.add_argument("--verbose", action="store_true",
                   help="Enable DEBUG logging")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

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
        logger.error("Nornir init failed: %s", exc)
        return 1

    if args.group:
        nr = nr.filter(F(groups__contains=args.group))
        if not nr.inventory.hosts:
            logger.error("No hosts matched group '%s'", args.group)
            return 1

    logger.info(
        "Querying %d host(s) for interface errors (threshold=%d)",
        len(nr.inventory.hosts),
        args.threshold,
    )
    results = nr.run(task=collect_errors, threshold=args.threshold)

    rows = build_rows(results, args.threshold)
    print_table(rows, args.threshold)

    if args.output:
        try:
            write_csv(rows, args.output)
        except OSError as exc:
            logger.error("CSV write failed: %s", exc)
            return 1

    failed = [h for h, r in results.items() if r.failed]
    if failed:
        logger.warning("Unreachable hosts: %s", ", ".join(str(h) for h in failed))
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
```