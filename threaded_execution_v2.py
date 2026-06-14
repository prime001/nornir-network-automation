NTP Audit - Network Time Protocol configuration and synchronization audit.

Purpose:
    Validates NTP configuration across network devices by checking:
    - Configured NTP servers match the expected policy
    - NTP synchronization is active and the clock is stable
    - Stratum level is within an acceptable threshold
    - No unauthorized NTP servers are configured

Usage:
    python 019_ntp_audit.py --username admin --password secret \
        --expected-servers 10.0.0.1 10.0.0.2

    python 019_ntp_audit.py --username admin --password secret \
        --expected-servers 10.0.0.1 --stratum-max 3 --output ntp_report.json

    python 019_ntp_audit.py --username admin --password secret \
        --expected-servers 10.0.0.1 --filter-site dc1

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Inventory: hosts.yaml, groups.yaml, defaults.yaml in the working directory
    or specify an alternate hosts file with --hosts.
"""

import argparse
import json
import logging
import re
import sys
from typing import Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_ntp_status(output: str) -> dict[str, Any]:
    """Extract sync state, stratum, and reference from 'show ntp status'."""
    info: dict[str, Any] = {"synced": False, "stratum": None, "reference": None}
    sync_match = re.search(r"Clock is\s+(\w+)", output, re.IGNORECASE)
    if sync_match:
        info["synced"] = sync_match.group(1).lower() == "synchronized"
    stratum_match = re.search(r"stratum\s+(\d+)", output, re.IGNORECASE)
    if stratum_match:
        info["stratum"] = int(stratum_match.group(1))
    ref_match = re.search(r"reference\s+(?:is\s+)?(\S+)", output, re.IGNORECASE)
    if ref_match:
        info["reference"] = ref_match.group(1)
    return info


def parse_ntp_associations(output: str) -> list[dict[str, Any]]:
    """Extract NTP peer entries from 'show ntp associations'."""
    peers = []
    ipv4 = r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    for line in output.splitlines():
        match = re.search(ipv4, line)
        if not match:
            continue
        stripped = line.strip()
        peers.append(
            {
                "address": match.group(0),
                "selected": stripped.startswith("*"),
                "candidate": stripped.startswith("+"),
            }
        )
    return peers


def audit_ntp(task: Task, expected_servers: list[str], stratum_max: int) -> Result:
    """Collect NTP status and associations, then evaluate policy compliance."""
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

    status = parse_ntp_status(status_r.result)
    peers = parse_ntp_associations(assoc_r.result)
    configured = {p["address"] for p in peers}
    expected_set = set(expected_servers)

    findings = []
    if not status["synced"]:
        findings.append("NTP clock is not synchronized")
    if status["stratum"] is not None and status["stratum"] > stratum_max:
        findings.append(
            f"Stratum {status['stratum']} exceeds allowed maximum of {stratum_max}"
        )
    missing = expected_set - configured
    if missing:
        findings.append(
            f"Required NTP servers not configured: {', '.join(sorted(missing))}"
        )
    extra = configured - expected_set
    if extra:
        findings.append(
            f"Unauthorized NTP servers present: {', '.join(sorted(extra))}"
        )

    report = {
        "host": task.host.name,
        "compliant": len(findings) == 0,
        "synced": status["synced"],
        "stratum": status["stratum"],
        "reference": status["reference"],
        "configured_servers": sorted(configured),
        "findings": findings,
    }
    return Result(host=task.host, result=report, failed=len(findings) > 0)


def build_nornir(hosts_file: str, username: str, password: str) -> Any:
    groups_file = hosts_file.replace("hosts", "groups")
    defaults_file = hosts_file.replace("hosts", "defaults")
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 10}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": hosts_file,
                "group_file": groups_file,
                "defaults_file": defaults_file,
            },
        },
    )
    nr.inventory.defaults.username = username
    nr.inventory.defaults.password = password
    return nr


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit NTP configuration and synchronization across network devices"
    )
    parser.add_argument(
        "--hosts", default="hosts.yaml",
        help="Nornir hosts inventory file (default: hosts.yaml)",
    )
    parser.add_argument("--username", "-u", required=True, help="Device username")
    parser.add_argument("--password", "-p", required=True, help="Device password")
    parser.add_argument(
        "--expected-servers", nargs="+", required=True, metavar="IP",
        help="One or more required NTP server IP addresses",
    )
    parser.add_argument(
        "--stratum-max", type=int, default=5,
        help="Maximum acceptable NTP stratum level (default: 5)",
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Write JSON report to FILE (default: stdout)",
    )
    parser.add_argument(
        "--filter-site", metavar="SITE",
        help="Restrict audit to hosts whose site group matches SITE",
    )
    args = parser.parse_args()

    nr = build_nornir(args.hosts, args.username, args.password)

    if args.filter_site:
        nr = nr.filter(site=args.filter_site)
        if not nr.inventory.hosts:
            logger.error("No hosts matched site filter: %s", args.filter_site)
            sys.exit(1)

    logger.info(
        "Auditing NTP on %d device(s); required servers: %s",
        len(nr.inventory.hosts),
        ", ".join(args.expected_servers),
    )

    agg = nr.run(
        task=audit_ntp,
        expected_servers=args.expected_servers,
        stratum_max=args.stratum_max,
    )

    reports = []
    for host, multi in agg.items():
        top = multi[0]
        if isinstance(top.result, dict):
            reports.append(top.result)
        else:
            err = str(top.exception) if top.exception else "unknown error"
            reports.append({"host": host, "compliant": False, "findings": [err]})

    compliant_count = sum(1 for r in reports if r.get("compliant"))
    summary = {
        "total": len(reports),
        "compliant": compliant_count,
        "non_compliant": len(reports) - compliant_count,
        "devices": reports,
    }

    payload = json.dumps(summary, indent=2)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(payload + "\n")
        logger.info("Report written to %s", args.output)
    else:
        print(payload)

    if compliant_count < len(reports):
        sys.exit(1)


if __name__ == "__main__":
    main()