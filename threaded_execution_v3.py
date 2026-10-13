ntp_audit.py - NTP Configuration and Synchronization Audit

Connects to network devices in parallel via Nornir and verifies:
  - NTP server configuration (expected servers present on every device)
  - NTP synchronization state (stratum, reference clock, offset)
  - Per-device compliance summary with exit code for CI integration

Usage:
    python ntp_audit.py --inventory hosts.yaml --groups-file groups.yaml \
        --expected-servers 10.0.0.1 10.0.0.2 --max-offset 500 --workers 20

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
    Devices must support IOS-style 'show ntp associations' / 'show ntp status'
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class NtpStatus:
    hostname: str
    configured_servers: List[str] = field(default_factory=list)
    synced: bool = False
    stratum: Optional[int] = None
    reference: Optional[str] = None
    offset_ms: Optional[float] = None
    error: Optional[str] = None


def parse_ntp_associations(output: str) -> List[str]:
    servers = []
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        candidate = parts[0].lstrip("*~+x-")
        if candidate and candidate[0].isdigit():
            servers.append(candidate)
    return servers


def parse_ntp_status(output: str) -> dict:
    result = {"synced": False, "stratum": None, "reference": None, "offset_ms": None}
    for line in output.splitlines():
        lower = line.lower()
        if "synchronized" in lower and "unsynchronized" not in lower:
            result["synced"] = True
        if "stratum" in lower:
            for token in line.split():
                if token.isdigit():
                    result["stratum"] = int(token)
                    break
        if "reference" in lower:
            parts = line.split()
            if len(parts) >= 2:
                result["reference"] = parts[-1]
        if "offset" in lower:
            for token in line.split():
                try:
                    result["offset_ms"] = float(token.rstrip(","))
                    break
                except ValueError:
                    continue
    return result


def collect_ntp(task: Task) -> Result:
    assoc = task.run(
        task=netmiko_send_command,
        command_string="show ntp associations",
        name="ntp-associations",
    )
    status = task.run(
        task=netmiko_send_command,
        command_string="show ntp status",
        name="ntp-status",
    )
    return Result(
        host=task.host,
        result={"associations": assoc.result, "status": status.result},
    )


def run_audit(
    inventory: str,
    groups_file: str,
    defaults_file: str,
    expected_servers: List[str],
    workers: int,
    filter_host: Optional[str],
    filter_groups: Optional[str],
) -> Dict[str, NtpStatus]:
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": inventory,
                "group_file": groups_file,
                "defaults_file": defaults_file,
            },
        },
    )

    if filter_host:
        nr = nr.filter(name=filter_host)
    if filter_groups:
        nr = nr.filter(groups=filter_groups.split(","))

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check inventory path and filter arguments.")
        sys.exit(1)

    logger.info(
        "Auditing NTP on %d host(s) with %d worker thread(s)",
        len(nr.inventory.hosts),
        workers,
    )

    raw = nr.run(task=collect_ntp, name="collect-ntp")

    results: Dict[str, NtpStatus] = {}
    for hostname, multi_result in raw.items():
        stat = NtpStatus(hostname=hostname)
        if multi_result.failed:
            stat.error = str(multi_result.exception or "connection failed")
            results[hostname] = stat
            continue
        try:
            data = multi_result[0].result
            stat.configured_servers = parse_ntp_associations(data["associations"])
            parsed = parse_ntp_status(data["status"])
            stat.synced = parsed["synced"]
            stat.stratum = parsed["stratum"]
            stat.reference = parsed["reference"]
            stat.offset_ms = parsed["offset_ms"]
        except Exception as exc:
            stat.error = f"parse error: {exc}"
        results[hostname] = stat

    return results


def print_report(
    results: Dict[str, NtpStatus],
    expected_servers: List[str],
    max_offset_ms: float,
) -> int:
    passed = failed = 0
    print(f"\n{'HOST':<30} {'SYNC':<6} {'STRAT':<7} {'OFFSET(ms)':<13} ISSUES")
    print("-" * 85)

    for hostname, stat in sorted(results.items()):
        issues = []

        if stat.error:
            issues.append(f"ERROR: {stat.error}")
        else:
            if not stat.synced:
                issues.append("not-synced")
            if expected_servers:
                missing = set(expected_servers) - set(stat.configured_servers)
                if missing:
                    issues.append(f"missing-servers:{','.join(sorted(missing))}")
            if stat.offset_ms is not None and abs(stat.offset_ms) > max_offset_ms:
                issues.append(f"offset-exceeded:{stat.offset_ms:.1f}ms")

        if issues:
            failed += 1
            marker = "!"
        else:
            passed += 1
            marker = " "

        sync_s = "OK" if stat.synced else "FAIL"
        strat_s = str(stat.stratum) if stat.stratum is not None else "?"
        off_s = f"{stat.offset_ms:.2f}" if stat.offset_ms is not None else "?"
        issue_s = "; ".join(issues) if issues else "—"
        print(f"{marker} {hostname:<28} {sync_s:<6} {strat_s:<7} {off_s:<13} {issue_s}")

    print("-" * 85)
    print(f"Hosts: {passed + failed}  Passed: {passed}  Failed: {failed}")
    return failed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit NTP sync and server config across devices using Nornir threaded runner."
    )
    p.add_argument("--inventory", default="hosts.yaml", help="Nornir hosts file (default: hosts.yaml)")
    p.add_argument("--groups-file", default="groups.yaml", help="Nornir groups file")
    p.add_argument("--defaults-file", default="defaults.yaml", help="Nornir defaults file")
    p.add_argument(
        "--expected-servers",
        nargs="*",
        default=[],
        metavar="IP",
        help="NTP server IPs required on every device",
    )
    p.add_argument(
        "--max-offset",
        type=float,
        default=500.0,
        metavar="MS",
        help="Max acceptable offset in milliseconds (default: 500)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Parallel worker threads (default: 20)",
    )
    p.add_argument("--filter-host", metavar="NAME", help="Limit to a single host by name")
    p.add_argument(
        "--filter-groups",
        metavar="G1,G2",
        help="Comma-separated group names to include",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    results = run_audit(
        inventory=args.inventory,
        groups_file=args.groups_file,
        defaults_file=args.defaults_file,
        expected_servers=args.expected_servers,
        workers=args.workers,
        filter_host=args.filter_host,
        filter_groups=args.filter_groups,
    )

    failures = print_report(results, args.expected_servers, args.max_offset)
    sys.exit(1 if failures else 0)