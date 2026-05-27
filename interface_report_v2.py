interface_error_report.py - Interface error counter and utilization report.

Collects per-interface error counters (RX/TX errors, input/output discards)
from network devices via NAPALM/Nornir, flags interfaces that exceed a
configurable threshold, and produces a tabular report or CSV export.

Useful for:
  - Detecting duplex mismatches, CRC errors, and oversubscribed uplinks
  - Scheduled health checks before/after maintenance windows
  - Baselining error rates for capacity planning

Usage:
    python interface_error_report.py --hosts rtr1,rtr2 --user admin --password s3cr3t
    python interface_error_report.py --hosts rtr1 --user admin --password s3cr3t \
        --platform eos --threshold 500 --flagged-only --output errors.csv

Prerequisites:
    pip install nornir nornir-napalm napalm tabulate
"""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from typing import List

from nornir.core import Nornir
from nornir.core.inventory import (
    ConnectionOptions,
    Defaults,
    Groups,
    Host,
    Hosts,
    Inventory,
)
from nornir.core.plugins.runners import ThreadedRunner
from nornir.core.state import GlobalState
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get
from tabulate import tabulate

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("if_error_report")


@dataclass
class IfCounter:
    host: str
    interface: str
    rx_errors: int
    tx_errors: int
    rx_discards: int
    tx_discards: int

    @property
    def total(self) -> int:
        return self.rx_errors + self.tx_errors + self.rx_discards + self.tx_discards


def _build_inventory(
    hostnames: List[str], username: str, password: str, platform: str
) -> Inventory:
    hosts = {}
    for name in hostnames:
        hosts[name] = Host(
            name=name,
            hostname=name,
            username=username,
            password=password,
            platform=platform,
            connection_options={
                "napalm": ConnectionOptions(
                    username=username,
                    password=password,
                    extras={"optional_args": {"timeout": 30}},
                )
            },
        )
    return Inventory(hosts=Hosts(hosts), groups=Groups(), defaults=Defaults())


def _collect(task: Task) -> Result:
    r = task.run(task=napalm_get, getters=["interfaces_counters"])
    counters = r[0].result.get("interfaces_counters", {})
    records = []
    for iface, s in counters.items():
        records.append(
            IfCounter(
                host=task.host.name,
                interface=iface,
                rx_errors=s.get("rx_errors") or 0,
                tx_errors=s.get("tx_errors") or 0,
                rx_discards=s.get("rx_discards") or 0,
                tx_discards=s.get("tx_discards") or 0,
            )
        )
    return Result(host=task.host, result=records)


def _render(records: List[IfCounter], threshold: int, flagged_only: bool) -> str:
    rows = sorted(records, key=lambda r: (r.host, r.interface))
    if flagged_only:
        rows = [r for r in rows if r.total >= threshold]
    table = [
        [
            r.host,
            r.interface,
            r.rx_errors,
            r.tx_errors,
            r.rx_discards,
            r.tx_discards,
            r.total,
            "!" if r.total >= threshold else "",
        ]
        for r in rows
    ]
    headers = ["Host", "Interface", "RX Err", "TX Err", "RX Drop", "TX Drop", "Total", "Flag"]
    return tabulate(table, headers=headers, tablefmt="grid")


def _write_csv(records: List[IfCounter], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["host", "interface", "rx_errors", "tx_errors", "rx_discards", "tx_discards", "total"])
        for r in records:
            w.writerow([r.host, r.interface, r.rx_errors, r.tx_errors, r.rx_discards, r.tx_discards, r.total])
    logger.info("CSV written to %s", path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Report interface error counters and flag threshold violations."
    )
    p.add_argument("--hosts", required=True, help="Comma-separated hostnames or IPs")
    p.add_argument("--user", required=True, help="SSH/API username")
    p.add_argument("--password", required=True, help="SSH/API password")
    p.add_argument(
        "--platform",
        default="ios",
        help="NAPALM driver: ios, eos, junos, nxos_ssh (default: ios)",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=1,
        help="Flag interfaces where total errors+discards >= N (default: 1)",
    )
    p.add_argument(
        "--flagged-only",
        action="store_true",
        help="Print only interfaces that breach the threshold",
    )
    p.add_argument("--output", metavar="FILE", help="Write full results to CSV file")
    p.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Concurrent worker threads (default: 10)",
    )
    p.add_argument("--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]
    if not host_list:
        logger.error("No valid hosts provided via --hosts")
        return 1

    nr = Nornir(
        inventory=_build_inventory(host_list, args.user, args.password, args.platform),
        runner=ThreadedRunner(num_workers=args.workers),
        data=GlobalState(),
    )

    logger.info("Querying %d host(s): %s", len(host_list), ", ".join(host_list))
    results = nr.run(task=_collect)

    all_records: List[IfCounter] = []
    failed: List[str] = []

    for hostname, multi in results.items():
        if multi.failed:
            logger.warning("Failed on %s: %s", hostname, multi[0].exception)
            failed.append(hostname)
            continue
        records = multi[0].result
        if isinstance(records, list):
            all_records.extend(records)

    if not all_records:
        logger.error("No interface data collected — check connectivity and credentials.")
        return 1

    print(_render(all_records, args.threshold, args.flagged_only))

    flagged_count = sum(1 for r in all_records if r.total >= args.threshold)
    logger.info(
        "Done — %d interface(s) on %d host(s) | %d flagged (threshold=%d) | %d failed",
        len(all_records),
        len(host_list) - len(failed),
        flagged_count,
        args.threshold,
        len(failed),
    )

    if args.output:
        _write_csv(all_records, args.output)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())