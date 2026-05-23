The `nornir-network-automation` repo isn't at `/opt/NetAutoCommitter` — that's a different project. Per the request to output only the script content:

---

"""
NTP Synchronization Audit — nornir-network-automation

Purpose:
    Audits NTP synchronization status across all inventory devices.
    Identifies unsynchronized clocks, high-stratum peers, and misconfigured
    time sources before they cause auth failures or log-correlation gaps.

Usage:
    python ntp_audit.py --inventory hosts.yaml --groups core,distribution
    python ntp_audit.py --inventory hosts.yaml --max-stratum 3 --output json
    python ntp_audit.py --inventory hosts.yaml --output csv > ntp_report.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
    Devices must allow 'show ntp status' and 'show ntp associations'
"""

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class NtpStatus:
    hostname: str
    synced: bool = False
    stratum: int = 16
    reference: str = ""
    peer_ip: str = ""
    offset_ms: float = 0.0
    error: str = ""
    flags: list = field(default_factory=list)


def parse_ntp_status(hostname: str, status_output: str, assoc_output: str) -> NtpStatus:
    result = NtpStatus(hostname=hostname)

    sync_match = re.search(
        r"Clock is (synchronized|unsynchronized).*?stratum\s+(\d+)"
        r"(?:,\s*reference is\s+([\d.]+))?",
        status_output,
        re.IGNORECASE,
    )
    if sync_match:
        result.synced = sync_match.group(1).lower() == "synchronized"
        result.stratum = int(sync_match.group(2))
        result.reference = sync_match.group(3) or ""

    offset_match = re.search(r"offset of ([\d.-]+) msec", status_output, re.IGNORECASE)
    if offset_match:
        result.offset_ms = float(offset_match.group(1))

    peer_match = re.search(r"^\*?([\d.]+)\s+\S+\s+(\d+)", assoc_output, re.MULTILINE)
    if peer_match:
        result.peer_ip = peer_match.group(1)

    if not sync_match and not peer_match:
        result.error = "Unable to parse NTP output (unsupported platform?)"

    return result


def collect_ntp(task: Task) -> Result:
    status_out = task.run(
        task=netmiko_send_command,
        command_string="show ntp status",
        name="ntp_status",
    )
    assoc_out = task.run(
        task=netmiko_send_command,
        command_string="show ntp associations",
        name="ntp_associations",
    )
    parsed = parse_ntp_status(
        task.host.name,
        status_out.result,
        assoc_out.result,
    )
    return Result(host=task.host, result=parsed)


def flag_issues(entry: NtpStatus, max_stratum: int) -> list:
    issues = []
    if not entry.synced:
        issues.append("UNSYNCED")
    if entry.stratum >= 16:
        issues.append("STRATUM-16")
    elif entry.stratum > max_stratum:
        issues.append(f"HIGH-STRATUM({entry.stratum})")
    if abs(entry.offset_ms) > 500:
        issues.append(f"LARGE-OFFSET({entry.offset_ms}ms)")
    if entry.error:
        issues.append("PARSE-ERROR")
    return issues


def render_table(entries: list) -> None:
    header = f"{'Host':<20} {'Sync':<6} {'Stratum':<8} {'Reference':<16} {'Peer':<16} {'Offset(ms)':<12} {'Flags'}"
    print(header)
    print("-" * len(header))
    for e in entries:
        flags = ",".join(e.flags) if e.flags else "OK"
        print(
            f"{e.hostname:<20} {'YES' if e.synced else 'NO':<6} {e.stratum:<8} "
            f"{e.reference:<16} {e.peer_ip:<16} {e.offset_ms:<12.1f} {flags}"
        )


def render_json(entries: list) -> None:
    out = []
    for e in entries:
        d = asdict(e)
        d["flags"] = e.flags
        out.append(d)
    print(json.dumps(out, indent=2))


def render_csv(entries: list) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(["hostname", "synced", "stratum", "reference", "peer_ip", "offset_ms", "flags", "error"])
    for e in entries:
        writer.writerow([
            e.hostname, e.synced, e.stratum, e.reference,
            e.peer_ip, e.offset_ms, "|".join(e.flags), e.error,
        ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit NTP synchronization across network devices")
    parser.add_argument("--inventory", default="hosts.yaml", help="Nornir hosts inventory file")
    parser.add_argument("--groups", help="Comma-separated group filter (e.g. core,distribution)")
    parser.add_argument("--max-stratum", type=int, default=4, help="Flag devices above this stratum (default: 4)")
    parser.add_argument("--output", choices=["table", "json", "csv"], default="table")
    parser.add_argument("--fail-only", action="store_true", help="Print only devices with issues")
    parser.add_argument("--workers", type=int, default=20, help="Parallel connection threads")
    parser.add_argument("--verbose", action="store_true", help="Show raw nornir task output")
    args = parser.parse_args()

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={"plugin": "SimpleInventory", "options": {"host_file": args.inventory}},
        logging={"enabled": False},
    )

    if args.groups:
        group_list = [g.strip() for g in args.groups.split(",")]
        nr = nr.filter(filter_func=lambda h: any(g in h.groups for g in group_list))

    if not nr.inventory.hosts:
        logger.error("No hosts matched the filter. Check --groups or inventory file.")
        sys.exit(1)

    results = nr.run(task=collect_ntp, name="NTP Audit")

    if args.verbose:
        print_result(results)

    entries = []
    for hostname, multi_result in results.items():
        if multi_result.failed:
            entry = NtpStatus(hostname=hostname, error=str(multi_result.exception))
            entry.flags = ["CONNECTION-FAILED"]
        else:
            entry = multi_result[0].result
            entry.flags = flag_issues(entry, args.max_stratum)
        entries.append(entry)

    entries.sort(key=lambda e: (not e.flags, e.hostname))

    if args.fail_only:
        entries = [e for e in entries if e.flags]

    if args.output == "table":
        render_table(entries)
        flagged = sum(1 for e in entries if e.flags)
        print(f"\nSummary: {len(entries)} devices audited, {flagged} with issues.")
    elif args.output == "json":
        render_json(entries)
    else:
        render_csv(entries)

    if any(e.flags for e in entries):
        sys.exit(1)


if __name__ == "__main__":
    main()