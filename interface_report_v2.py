Now I'll write the script. Since 003 and 013 are already interface reports, I'll make 023 focused on error-threshold monitoring with multi-format export — a distinct, practical use case.

```python
"""
023_interface_report.py — Interface Error Threshold Monitor

Purpose:
    Polls network devices via NAPALM/Nornir and reports interface error
    counters (input errors, output errors, CRC errors, resets, discards).
    Flags any interface whose error rate exceeds configurable thresholds.
    Useful for proactive fault detection before circuits fully degrade.

Usage:
    # Single device, print table
    python 023_interface_report.py --host 192.168.1.1 --username admin

    # Multiple hosts from inventory, export CSV
    python 023_interface_report.py --inventory hosts.yaml \
        --group core-routers --output csv --out-file errors.csv

    # Raise alert threshold to 0.5% error rate
    python 023_interface_report.py --host 192.168.1.1 \
        --username admin --error-pct 0.5

    # Only show interfaces with errors above threshold (quiet mode)
    python 023_interface_report.py --host 192.168.1.1 \
        --username admin --errors-only

Prerequisites:
    pip install nornir nornir-napalm napalm tabulate
    NAPALM-compatible device (IOS, EOS, NXOS, JunOS, IOS-XR)
"""

import argparse
import csv
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional

from nornir import InitNornir
from nornir.core.inventory import Defaults, Host, Hosts, Groups, Group
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get
from tabulate import tabulate

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("interface_error_monitor")


@dataclass
class InterfaceErrorRow:
    host: str
    interface: str
    is_up: bool
    speed_mbps: Optional[float]
    rx_errors: int
    tx_errors: int
    rx_discards: int
    tx_discards: int
    rx_packets: int
    crc_errors: int
    error_pct: float
    threshold_exceeded: bool


def _parse_speed(speed_val) -> Optional[float]:
    """Convert NAPALM speed value (bps or -1) to Mbps."""
    try:
        bps = float(speed_val)
        return round(bps / 1_000_000, 1) if bps > 0 else None
    except (TypeError, ValueError):
        return None


def collect_interface_errors(task: Task, error_pct_threshold: float) -> Result:
    results = task.run(
        task=napalm_get,
        getters=["interfaces", "interfaces_counters"],
    )

    interfaces = results[0].result.get("interfaces", {})
    counters = results[0].result.get("interfaces_counters", {})

    rows = []
    for iface, stats in counters.items():
        meta = interfaces.get(iface, {})
        rx_pkts = stats.get("rx_unicast_packets", 0) or 0
        rx_err = stats.get("rx_errors", 0) or 0
        tx_err = stats.get("tx_errors", 0) or 0
        rx_disc = stats.get("rx_discards", 0) or 0
        tx_disc = stats.get("tx_discards", 0) or 0
        crc = stats.get("rx_no_buffer", 0) or 0  # proxy; driver-dependent

        total_err = rx_err + tx_err
        pct = (total_err / rx_pkts * 100) if rx_pkts > 0 else 0.0

        rows.append(InterfaceErrorRow(
            host=task.host.name,
            interface=iface,
            is_up=meta.get("is_up", False),
            speed_mbps=_parse_speed(meta.get("speed")),
            rx_errors=rx_err,
            tx_errors=tx_err,
            rx_discards=rx_disc,
            tx_discards=tx_disc,
            rx_packets=rx_pkts,
            crc_errors=crc,
            error_pct=round(pct, 4),
            threshold_exceeded=(pct >= error_pct_threshold),
        ))

    rows.sort(key=lambda r: (-r.error_pct, r.interface))
    return Result(host=task.host, result=rows)


def build_single_host_nornir(host: str, username: str, password: str,
                              platform: str) -> "InitNornir":
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": None,
                "group_file": None,
                "defaults_file": None,
            },
        },
        logging={"enabled": False},
    )


def build_nornir_from_hosts(host: str, username: str, password: str,
                             platform: str):
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {"host_file": None},
        },
        logging={"enabled": False},
    )
    nr.inventory.hosts[host] = Host(
        name=host,
        hostname=host,
        username=username,
        password=password,
        platform=platform,
        groups=[],
        defaults=Defaults(),
    )
    nr.inventory.hosts[host].groups = Groups()
    return nr


def print_table(rows, errors_only: bool) -> None:
    display = [r for r in rows if r.threshold_exceeded] if errors_only else rows
    if not display:
        print("No interfaces to display.")
        return

    headers = ["Host", "Interface", "Up", "Speed(M)", "RxErr", "TxErr",
               "RxDisc", "TxDisc", "RxPkts", "Err%", "!"]
    table = [
        [
            r.host, r.interface, "Y" if r.is_up else "N",
            r.speed_mbps or "-",
            r.rx_errors, r.tx_errors, r.rx_discards, r.tx_discards,
            r.rx_packets,
            f"{r.error_pct:.4f}",
            "<<" if r.threshold_exceeded else "",
        ]
        for r in display
    ]
    print(tabulate(table, headers=headers, tablefmt="simple"))


def write_csv(rows, path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))
    logger.warning("CSV written to %s", path)


def write_json(rows, path: str) -> None:
    with open(path, "w") as f:
        json.dump([asdict(r) for r in rows], f, indent=2)
    logger.warning("JSON written to %s", path)


def main():
    parser = argparse.ArgumentParser(
        description="Interface error threshold monitor using Nornir/NAPALM"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", help="Single device hostname or IP")
    target.add_argument("--inventory", help="Path to Nornir hosts.yaml")

    parser.add_argument("--username", default=os.environ.get("NET_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("NET_PASS"))
    parser.add_argument("--platform", default="ios",
                        help="NAPALM driver: ios, eos, nxos, junos (default: ios)")
    parser.add_argument("--group", help="Filter inventory by Nornir group")
    parser.add_argument("--error-pct", type=float, default=0.1,
                        help="Error %% threshold to flag (default: 0.1)")
    parser.add_argument("--errors-only", action="store_true",
                        help="Show only interfaces exceeding threshold")
    parser.add_argument("--output", choices=["table", "json", "csv"],
                        default="table")
    parser.add_argument("--out-file", help="Output file path (json/csv modes)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    password = args.password or os.environ.get("NET_PASS") or ""
    if not password:
        import getpass
        password = getpass.getpass(f"Password for {args.username}: ")

    if args.host:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": 1}},
            inventory={"plugin": "SimpleInventory",
                       "options": {"host_file": None}},
            logging={"enabled": False},
        )
        nr.inventory.hosts[args.host] = Host(
            name=args.host,
            hostname=args.host,
            username=args.username,
            password=password,
            platform=args.platform,
            groups=Groups(),
            defaults=Defaults(),
        )
    else:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": 10}},
            inventory={"plugin": "SimpleInventory",
                       "options": {"host_file": args.inventory}},
            logging={"enabled": False},
        )
        if args.group:
            nr = nr.filter(groups__contains=args.group)

    results = nr.run(
        task=collect_interface_errors,
        error_pct_threshold=args.error_pct,
    )

    all_rows = []
    for host_name, multi in results.items():
        if multi.failed:
            print(f"[ERROR] {host_name}: {multi.exception}", file=sys.stderr)
            continue
        host_rows = multi[1].result if len(multi) > 1 else []
        all_rows.extend(host_rows)

    if not all_rows:
        print("No data collected.")
        sys.exit(1)

    if args.output == "table":
        print_table(all_rows, args.errors_only)
    elif args.output == "csv":
        out = args.out_file or "interface_errors.csv"
        write_csv(all_rows, out)
    elif args.output == "json":
        out = args.out_file or "interface_errors.json"
        write_json(all_rows, out)

    flagged = sum(1 for r in all_rows if r.threshold_exceeded)
    print(f"\nSummary: {len(all_rows)} interfaces checked, "
          f"{flagged} exceeded {args.error_pct}% error threshold.")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
```