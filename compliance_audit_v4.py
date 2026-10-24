NTP Compliance Auditor

Purpose:
    Audits NTP configuration and synchronization state across a fleet of network
    devices. For each device it verifies that NTP is actively synchronized, that
    the stratum level is within an acceptable ceiling, and that the minimum number
    of reachable NTP peers is present.  Results are summarised in a PASS/FAIL
    report with per-host issue details.

Usage:
    # Single device
    python ntp_compliance_audit.py --host 192.168.1.1 -u admin -p secret

    # Inventory file, filtered to a group
    python ntp_compliance_audit.py --inventory hosts.yaml --groups core_routers

    # Raise the stratum ceiling and require three peers
    python ntp_compliance_audit.py --host 10.0.0.1 -u admin -p secret \
        --max-stratum 4 --min-servers 3

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko

    Devices must permit SSH and support 'show ntp status' / 'show ntp associations'.
    Regex patterns target Cisco IOS/IOS-XE output; adjust for other platforms.
"""

import argparse
import logging
import re
import sys
from typing import Any, Dict, List, Tuple

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ntp_audit")

DEFAULT_MIN_SERVERS = 2
DEFAULT_MAX_STRATUM = 3

_SYNCED_RE = re.compile(r"Clock is synchronized", re.IGNORECASE)
_STRATUM_RE = re.compile(r"stratum\s+(\d+)", re.IGNORECASE)
_PEER_RE = re.compile(r"^\s*[*~+]\s+(\S+)", re.MULTILINE)


def _parse_ntp_status(output: str, max_stratum: int) -> Tuple[bool, Any, List[str]]:
    synced = bool(_SYNCED_RE.search(output))
    m = _STRATUM_RE.search(output)
    stratum = int(m.group(1)) if m else None

    issues: List[str] = []
    if not synced:
        issues.append("NTP not synchronized")
    if stratum is None:
        issues.append("stratum not reported")
    elif stratum > max_stratum:
        issues.append(f"stratum {stratum} exceeds limit of {max_stratum}")

    return synced, stratum, issues


def _parse_ntp_associations(output: str, min_servers: int) -> Tuple[List[str], List[str]]:
    peers = _PEER_RE.findall(output)
    issues: List[str] = []
    if len(peers) < min_servers:
        issues.append(f"{len(peers)} reachable peer(s), minimum is {min_servers}")
    return peers, issues


def audit_ntp(task: Task, min_servers: int, max_stratum: int) -> Result:
    status_r = task.run(
        task=netmiko_send_command,
        command_string="show ntp status",
        name="ntp_status",
    )
    assoc_r = task.run(
        task=netmiko_send_command,
        command_string="show ntp associations",
        name="ntp_associations",
    )

    synced, stratum, status_issues = _parse_ntp_status(status_r.result, max_stratum)
    peers, assoc_issues = _parse_ntp_associations(assoc_r.result, min_servers)
    all_issues = status_issues + assoc_issues

    summary: Dict[str, Any] = {
        "compliant": len(all_issues) == 0,
        "synced": synced,
        "stratum": stratum,
        "reachable_peers": peers,
        "issues": all_issues,
    }
    return Result(host=task.host, result=summary, changed=False)


def _build_nornir(args: argparse.Namespace):
    if args.inventory:
        return InitNornir(
            inventory={
                "plugin": "SimpleInventory",
                "options": {"host_file": args.inventory},
            },
            runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        )
    return InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "hosts": {
                    args.host: {
                        "hostname": args.host,
                        "username": args.username,
                        "password": args.password,
                        "platform": args.platform,
                        "port": args.port,
                    }
                }
            },
        },
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
    )


def _print_report(results) -> int:
    passed: List[str] = []
    failed: List[Tuple[str, str]] = []

    for host, mr in results.items():
        if mr.failed:
            failed.append((host, f"connection error: {mr.exception}"))
            continue
        summary: Dict[str, Any] = mr[0].result
        if summary["compliant"]:
            passed.append(host)
        else:
            failed.append((host, "; ".join(summary["issues"])))

    print("\n=== NTP Compliance Report ===")
    if passed:
        print(f"PASS ({len(passed)}): {', '.join(sorted(passed))}")
    if failed:
        print(f"FAIL ({len(failed)}):")
        for host, reason in sorted(failed):
            print(f"  {host}: {reason}")
    total = len(passed) + len(failed)
    print(f"\nCompliance rate: {len(passed)}/{total}")
    return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit NTP synchronisation compliance across network devices"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", metavar="HOST", help="Single device hostname or IP")
    target.add_argument(
        "--inventory", metavar="FILE", help="Nornir SimpleInventory hosts.yaml"
    )
    parser.add_argument("-u", "--username", default="admin", help="SSH username")
    parser.add_argument("-p", "--password", default="", help="SSH password")
    parser.add_argument(
        "--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)"
    )
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument(
        "--groups", nargs="*", metavar="GROUP",
        help="Inventory groups to target (requires --inventory)"
    )
    parser.add_argument(
        "--min-servers", type=int, default=DEFAULT_MIN_SERVERS,
        help=f"Minimum reachable NTP peers required (default: {DEFAULT_MIN_SERVERS})",
    )
    parser.add_argument(
        "--max-stratum", type=int, default=DEFAULT_MAX_STRATUM,
        help=f"Maximum allowed NTP stratum (default: {DEFAULT_MAX_STRATUM})",
    )
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Concurrent SSH workers (default: 10)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print raw Nornir results in addition to the summary"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = _build_nornir(args)

    if args.groups and args.inventory:
        nr = nr.filter(lambda h: any(g in (h.groups or []) for g in args.groups))

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check --inventory / --groups arguments.")
        sys.exit(1)

    logger.info("Auditing %d host(s)", len(nr.inventory.hosts))
    results = nr.run(
        task=audit_ntp,
        min_servers=args.min_servers,
        max_stratum=args.max_stratum,
    )

    if args.verbose:
        print_result(results)

    sys.exit(_print_report(results))