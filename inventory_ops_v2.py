The task asks for script output only. Writing the NTP status audit script — a practical inventory-ops script not covered by any existing v1/v2 scripts.

"""
NTP Status Audit — nornir-network-automation

Purpose:
    Collect NTP synchronization status across a network fleet.
    Reports stratum, reference server, clock offset, and sync state
    per device. Identifies unsynchronized nodes that risk log-timestamp
    skew, certificate validation failures, and compliance gaps.

Usage:
    python 017_ntp_status.py --inventory hosts.yaml
    python 017_ntp_status.py --inventory hosts.yaml --filter-group core_routers
    python 017_ntp_status.py --inventory hosts.yaml --filter-site dc1 --csv report.csv
    python 017_ntp_status.py --inventory hosts.yaml --username admin --password secret

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
    Supported platforms: cisco_ios, cisco_nxos, arista_eos
"""

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class NtpStatus:
    hostname: str
    synced: bool = False
    stratum: Optional[int] = None
    reference: str = ""
    offset_ms: Optional[float] = None
    error: str = ""


def _parse_ios_ntp(output: str) -> Dict:
    data: Dict = {"synced": False, "stratum": None, "reference": "", "offset_ms": None}
    if re.search(r"Clock is synchronized", output, re.IGNORECASE):
        data["synced"] = True
    m = re.search(r"stratum\s+(\d+)", output, re.IGNORECASE)
    if m:
        data["stratum"] = int(m.group(1))
    m = re.search(r"reference is\s+(\S+)", output, re.IGNORECASE)
    if m:
        data["reference"] = m.group(1)
    m = re.search(r"offset\s+([-\d.]+)\s+msec", output, re.IGNORECASE)
    if m:
        data["offset_ms"] = float(m.group(1))
    return data


def _parse_eos_ntp(output: str) -> Dict:
    data: Dict = {"synced": False, "stratum": None, "reference": "", "offset_ms": None}
    if re.search(r"synchronised", output, re.IGNORECASE):
        data["synced"] = True
    m = re.search(r"stratum\s+(\d+)", output, re.IGNORECASE)
    if m:
        data["stratum"] = int(m.group(1))
    m = re.search(r"reference ID\s+[:\s]+(\S+)", output, re.IGNORECASE)
    if m:
        data["reference"] = m.group(1)
    m = re.search(r"offset\s+([-\d.]+)\s+ms", output, re.IGNORECASE)
    if m:
        data["offset_ms"] = float(m.group(1))
    return data


def collect_ntp_status(task: Task) -> Result:
    platform = (task.host.platform or "cisco_ios").lower()
    try:
        r = task.run(
            task=netmiko_send_command,
            command_string="show ntp status",
            name="show ntp status",
        )
        if "eos" in platform or "arista" in platform:
            parsed = _parse_eos_ntp(r.result)
        else:
            parsed = _parse_ios_ntp(r.result)
        return Result(host=task.host, result=parsed)
    except Exception as exc:
        logger.error("Failed on %s: %s", task.host.name, exc)
        return Result(host=task.host, result={"error": str(exc)}, failed=True)


def _print_table(statuses: List[NtpStatus]) -> None:
    col = {"host": 22, "sync": 8, "stratum": 9, "ref": 20, "offset": 12}
    hdr = (
        f"{'Host':<{col['host']}} {'Synced':<{col['sync']}} {'Stratum':<{col['stratum']}}"
        f" {'Reference':<{col['ref']}} {'Offset(ms)':<{col['offset']}} Error"
    )
    print(hdr)
    print("-" * (len(hdr) + 10))
    for s in statuses:
        sync_str = "YES" if s.synced else ("ERR" if s.error else "NO ")
        stratum_str = str(s.stratum) if s.stratum is not None else "-"
        offset_str = f"{s.offset_ms:.3f}" if s.offset_ms is not None else "-"
        print(
            f"{s.hostname:<{col['host']}} {sync_str:<{col['sync']}} {stratum_str:<{col['stratum']}}"
            f" {s.reference:<{col['ref']}} {offset_str:<{col['offset']}} {s.error}"
        )


def _write_csv(statuses: List[NtpStatus], path: str) -> None:
    fields = ["hostname", "synced", "stratum", "reference", "offset_ms", "error"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in statuses:
            w.writerow({
                "hostname": s.hostname,
                "synced": s.synced,
                "stratum": s.stratum if s.stratum is not None else "",
                "reference": s.reference,
                "offset_ms": s.offset_ms if s.offset_ms is not None else "",
                "error": s.error,
            })


def run_audit(nr, csv_path: Optional[str] = None) -> List[NtpStatus]:
    agg = nr.run(task=collect_ntp_status, name="NTP status")

    statuses: List[NtpStatus] = []
    for hostname, multi in agg.items():
        status = NtpStatus(hostname=hostname)
        task_result = multi[0]
        if task_result.failed or isinstance(task_result.result, dict) and task_result.result.get("error"):
            status.error = str(
                task_result.exception or
                (task_result.result.get("error") if isinstance(task_result.result, dict) else "") or
                "task failed"
            )
        elif isinstance(task_result.result, dict):
            data = task_result.result
            status.synced = data.get("synced", False)
            status.stratum = data.get("stratum")
            status.reference = data.get("reference", "")
            status.offset_ms = data.get("offset_ms")
        statuses.sort(key=lambda s: (s.synced, s.hostname))

    statuses.sort(key=lambda s: (s.synced, s.hostname))
    _print_table(statuses)

    if csv_path:
        _write_csv(statuses, csv_path)
        print(f"\nReport saved to {csv_path}")

    total = len(statuses)
    errored = sum(1 for s in statuses if s.error)
    synced = sum(1 for s in statuses if s.synced)
    unsynced = total - synced - errored
    print(f"\nSummary: {total} devices — {synced} synced, {unsynced} unsynced, {errored} errors")
    return statuses


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit NTP synchronization status across network devices"
    )
    p.add_argument("--inventory", default="hosts.yaml", help="Nornir hosts file")
    p.add_argument("--groups-file", default="groups.yaml", help="Nornir groups file")
    p.add_argument("--defaults-file", default="defaults.yaml", help="Nornir defaults file")
    p.add_argument("--filter-site", help="Filter hosts by data.site value")
    p.add_argument("--filter-group", help="Filter hosts by Nornir group membership")
    p.add_argument("--username", help="Override credential username")
    p.add_argument("--password", help="Override credential password")
    p.add_argument("--csv", dest="csv_path", metavar="FILE", help="Write CSV report to FILE")
    p.add_argument("--workers", type=int, default=20, help="Concurrent workers (default: 20)")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.inventory,
                "group_file": args.groups_file,
                "defaults_file": args.defaults_file,
            },
        },
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
    )

    if args.username or args.password:
        for host in nr.inventory.hosts.values():
            if args.username:
                host.username = args.username
            if args.password:
                host.password = args.password

    if args.filter_group:
        nr = nr.filter(F(groups__contains=args.filter_group))
    if args.filter_site:
        nr = nr.filter(F(data__site=args.filter_site))

    if not nr.inventory.hosts:
        print("No hosts matched the filter criteria.", file=sys.stderr)
        sys.exit(1)

    print(f"Running NTP audit against {len(nr.inventory.hosts)} device(s)...\n")
    run_audit(nr, csv_path=args.csv_path)