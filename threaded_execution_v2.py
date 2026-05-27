ntp_audit.py - Fleet-wide NTP compliance audit using Nornir threaded execution.

Purpose:
    Connects to all devices in the Nornir inventory concurrently, collects NTP
    synchronization status and configured server list, then flags devices that
    are unsynced, exceed a stratum threshold, or are missing required NTP peers.
    Optionally writes a CSV report for change-management evidence.

Usage:
    python ntp_audit.py [options]

    python ntp_audit.py --expected-servers 10.0.1.10 10.0.1.11 --max-stratum 4
    python ntp_audit.py --filter-group datacenter --output ntp_report.csv
    python ntp_audit.py --username admin --password secret --workers 20

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    hosts.yaml / groups.yaml / defaults.yaml in cwd (or pass --inventory <dir>)
    Devices must support IOS-style 'show ntp status' and 'show ntp associations'.
"""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ntp_audit")


@dataclass
class NTPStatus:
    hostname: str
    synced: bool = False
    stratum: int = 16
    configured_servers: List[str] = field(default_factory=list)
    active_peer: str = ""
    offset_ms: float = 0.0
    error: Optional[str] = None


def _parse_associations(output: str) -> dict:
    """Return {ip: {active: bool}} from 'show ntp associations' output."""
    peers = {}
    for line in output.splitlines():
        active = line.lstrip().startswith("*")
        candidate = line.strip().lstrip("*+~x# ")
        parts = candidate.split()
        if parts and parts[0].count(".") == 3:
            peers[parts[0]] = {"active": active}
    return peers


def _parse_status(output: str) -> tuple:
    """Return (synced: bool, stratum: int, offset_ms: float) from 'show ntp status'."""
    lower = output.lower()
    synced = "synchronized" in lower and "unsynchronized" not in lower
    stratum, offset = 16, 0.0
    for line in output.splitlines():
        lline = line.lower()
        if "stratum" in lline:
            for token in line.split(","):
                if "stratum" in token.lower():
                    try:
                        stratum = int(token.strip().split()[-1])
                    except (ValueError, IndexError):
                        pass
        if "offset" in lline:
            for token in line.split(","):
                if "offset" in token.lower():
                    try:
                        offset = float(token.strip().split()[-1])
                    except (ValueError, IndexError):
                        pass
    return synced, stratum, offset


def audit_ntp(task: Task) -> Result:
    """Nornir task: gather NTP state from one device and return NTPStatus."""
    status = NTPStatus(hostname=task.host.name)
    try:
        assoc = task.run(
            task=netmiko_send_command,
            command_string="show ntp associations",
            use_textfsm=False,
        )
        stat = task.run(
            task=netmiko_send_command,
            command_string="show ntp status",
            use_textfsm=False,
        )
        peers = _parse_associations(assoc.result)
        status.configured_servers = list(peers)
        active = [ip for ip, v in peers.items() if v["active"]]
        status.active_peer = active[0] if active else ""
        status.synced, status.stratum, status.offset_ms = _parse_status(stat.result)
    except Exception as exc:
        status.error = str(exc)
        logger.error("%s: %s", task.host.name, exc)
    return Result(host=task.host, result=status)


def _violations(status: NTPStatus, expected: List[str], max_stratum: int) -> List[str]:
    if status.error:
        return [f"connection error: {status.error}"]
    issues = []
    if not status.synced:
        issues.append("NTP not synchronized")
    if status.stratum > max_stratum:
        issues.append(f"stratum {status.stratum} exceeds max {max_stratum}")
    missing = set(expected) - set(status.configured_servers)
    if missing:
        issues.append(f"missing required servers: {', '.join(sorted(missing))}")
    return issues


def _write_csv(rows: list, path: str) -> None:
    fieldnames = ["hostname", "synced", "stratum", "active_peer",
                  "offset_ms", "configured_servers", "violations"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Report written to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NTP compliance audit across Nornir inventory (threaded)"
    )
    parser.add_argument("--inventory", default=".",
                        help="Directory containing hosts/groups/defaults YAML (default: .)")
    parser.add_argument("--expected-servers", nargs="*", default=[], metavar="IP",
                        help="NTP server IPs required on every device")
    parser.add_argument("--max-stratum", type=int, default=5,
                        help="Maximum acceptable stratum (default: 5)")
    parser.add_argument("--username", help="Override inventory username")
    parser.add_argument("--password", help="Override inventory password")
    parser.add_argument("--workers", type=int, default=10,
                        help="Thread pool size (default: 10)")
    parser.add_argument("--filter-group", dest="group",
                        help="Limit audit to a specific Nornir host group")
    parser.add_argument("--output", metavar="FILE",
                        help="Write CSV report to FILE")
    parser.add_argument("--verbose", action="store_true",
                        help="Print raw Nornir task output")
    args = parser.parse_args()

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": f"{args.inventory}/hosts.yaml",
                "group_file": f"{args.inventory}/groups.yaml",
                "defaults_file": f"{args.inventory}/defaults.yaml",
            },
        },
    )
    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password
    if args.group:
        nr = nr.filter(groups=args.group)

    device_count = len(nr.inventory.hosts)
    print(f"Auditing NTP on {device_count} device(s) with {args.workers} workers...\n")

    results = nr.run(task=audit_ntp, name="NTP Audit")

    if args.verbose:
        print_result(results)

    rows, passed, failed = [], 0, 0
    for hostname, multi in results.items():
        status: NTPStatus = multi[0].result
        issues = _violations(status, args.expected_servers, args.max_stratum)
        ok = not issues
        passed += ok
        failed += not ok
        label = "PASS" if ok else "FAIL"

        print(f"[{label}] {hostname}")
        print(f"       synced={status.synced}  stratum={status.stratum}"
              f"  active_peer={status.active_peer or 'none'}"
              f"  offset={status.offset_ms} ms")
        for issue in issues:
            print(f"       ! {issue}")
        if status.configured_servers:
            print(f"       servers: {', '.join(status.configured_servers)}")
        print()

        rows.append({
            "hostname": hostname,
            "synced": status.synced,
            "stratum": status.stratum,
            "active_peer": status.active_peer,
            "offset_ms": status.offset_ms,
            "configured_servers": "|".join(status.configured_servers),
            "violations": "; ".join(issues),
        })

    print(f"Summary: {passed} passed, {failed} failed out of {passed + failed} devices.")
    if args.output:
        _write_csv(rows, args.output)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()