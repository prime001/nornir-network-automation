```
"""
Interface Error Counter Analyzer
=================================
Purpose:
    Collects interface error counters from IOS/IOS-XE/NX-OS devices via
    Nornir and reports interfaces exceeding configurable thresholds. Useful
    for catching degraded physical links (CRC storms, excessive drops) before
    they cause service-impacting outages.

Usage:
    python 042_interface_error_counters.py --host core-sw1 --username admin --password secret
    python 042_interface_error_counters.py --group access --threshold 100 --csv errors.csv
    python 042_interface_error_counters.py --host rtr1 --all-interfaces

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
"""

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class InterfaceErrors:
    name: str
    input_errors: int = 0
    output_drops: int = 0
    crc_errors: int = 0
    runts: int = 0
    giants: int = 0

    def total(self) -> int:
        return self.input_errors + self.output_drops + self.crc_errors + self.runts + self.giants


def parse_ios_errors(output: str) -> List[InterfaceErrors]:
    interfaces: List[InterfaceErrors] = []
    current = None

    for line in output.splitlines():
        m = re.match(r"^(\S+) is", line)
        if m:
            if current:
                interfaces.append(current)
            current = InterfaceErrors(name=m.group(1))
            continue

        if current is None:
            continue

        for pattern, attr in [
            (r"(\d+)\s+input errors", "input_errors"),
            (r"(\d+)\s+CRC", "crc_errors"),
            (r"(\d+)\s+output drops", "output_drops"),
            (r"(\d+)\s+runts", "runts"),
            (r"(\d+)\s+giants", "giants"),
        ]:
            m = re.search(pattern, line)
            if m:
                setattr(current, attr, int(m.group(1)))

    if current:
        interfaces.append(current)
    return interfaces


def collect_errors(task: Task) -> Result:
    r = task.run(
        task=netmiko_send_command,
        command_string="show interfaces",
        use_textfsm=False,
    )
    return Result(host=task.host, result=parse_ios_errors(r.result))


def write_csv(rows: List[Dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Report interfaces exceeding error counter thresholds"
    )
    p.add_argument("--host", help="Target a specific host from inventory")
    p.add_argument("--group", help="Target a device group from inventory")
    p.add_argument("--username", help="Override inventory username")
    p.add_argument("--password", help="Override inventory password")
    p.add_argument(
        "--threshold",
        type=int,
        default=0,
        help="Minimum total errors to report (default: 0 = any errors)",
    )
    p.add_argument(
        "--all-interfaces",
        action="store_true",
        help="Include zero-error interfaces",
    )
    p.add_argument("--csv", metavar="FILE", help="Write results to CSV")
    p.add_argument(
        "--config",
        default="config.yaml",
        help="Nornir config file (default: config.yaml)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config)
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        sys.exit(1)

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if args.host:
        nr = nr.filter(F(name=args.host))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))

    if not nr.inventory.hosts:
        print("No hosts matched.", file=sys.stderr)
        sys.exit(1)

    print(f"Polling {len(nr.inventory.hosts)} host(s)...")
    results = nr.run(task=collect_errors, name="collect_errors")

    csv_rows: List[Dict] = []
    total_flagged = 0

    for host, multi in results.items():
        if multi.failed:
            print(f"[ERROR] {host}: {multi.exception}")
            continue

        ifaces: List[InterfaceErrors] = multi[0].result
        flagged = [
            i for i in ifaces
            if args.all_interfaces or i.total() > args.threshold
        ]

        if not flagged:
            print(f"{host}: no interfaces above threshold")
            continue

        print(f"\n{'=' * 70}")
        print(f"Host: {host}  ({len(flagged)} interface(s))")
        print(f"{'=' * 70}")
        print(
            f"{'Interface':<32} {'InErr':>7} {'CRC':>7} {'OutDrop':>8}"
            f" {'Runts':>6} {'Giants':>7} {'Total':>7}"
        )
        print("-" * 76)

        for i in sorted(flagged, key=lambda x: x.total(), reverse=True):
            print(
                f"{i.name:<32} {i.input_errors:>7} {i.crc_errors:>7}"
                f" {i.output_drops:>8} {i.runts:>6} {i.giants:>7} {i.total():>7}"
            )
            csv_rows.append(
                {
                    "host": host,
                    "interface": i.name,
                    "input_errors": i.input_errors,
                    "crc_errors": i.crc_errors,
                    "output_drops": i.output_drops,
                    "runts": i.runts,
                    "giants": i.giants,
                    "total_errors": i.total(),
                }
            )
            total_flagged += 1

    print(f"\nTotal interfaces reported: {total_flagged}")

    if args.csv:
        write_csv(csv_rows, args.csv)
        print(f"Results saved to {args.csv}")


if __name__ == "__main__":
    main()
```