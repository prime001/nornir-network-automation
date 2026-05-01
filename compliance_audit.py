```python
"""
006_compliance_audit.py — Security compliance audit via Nornir + Netmiko

Purpose:
    Checks Cisco IOS/IOS-XE devices against a baseline security policy and
    produces a per-device pass/fail report. Useful as a pre-change gate or
    recurring audit job in a CI pipeline.

Compliance checks performed:
    - SSH v2 enforced (no SSHv1)
    - HTTP/HTTPS management server disabled
    - Service password-encryption enabled
    - NTP server(s) configured
    - SNMP v1/v2c read-write community strings absent
    - Console / VTY exec-timeout set
    - Logging host configured

Usage:
    python 006_compliance_audit.py --inventory hosts.yaml --groups core,edge
    python 006_compliance_audit.py --inventory hosts.yaml --host rtr-01 --fail-fast
    python 006_compliance_audit.py --inventory hosts.yaml --output report.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    A Nornir inventory (hosts.yaml / groups.yaml / defaults.yaml) with
    platform set to "cisco_ios" or "cisco_xe" and valid credentials.
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("compliance_audit")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class HostReport:
    hostname: str
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)


def _check(config: str, name: str, pattern: str, expect_match: bool, detail_fail: str) -> CheckResult:
    matched = bool(re.search(pattern, config, re.MULTILINE))
    passed = matched if expect_match else not matched
    return CheckResult(name=name, passed=passed, detail="" if passed else detail_fail)


def run_compliance_checks(task: Task) -> Result:
    output = task.run(
        task=netmiko_send_command,
        command_string="show running-config",
        use_textfsm=False,
    )
    config: str = output.result

    checks = [
        _check(config, "ssh-v2",
               r"^ip ssh version 2", expect_match=True,
               detail_fail="'ip ssh version 2' not found — SSHv1 may be accepted"),
        _check(config, "no-http-server",
               r"^ip http server\b", expect_match=False,
               detail_fail="'ip http server' is enabled"),
        _check(config, "no-https-server",
               r"^ip http secure-server\b", expect_match=False,
               detail_fail="'ip http secure-server' is enabled"),
        _check(config, "password-encryption",
               r"^service password-encryption", expect_match=True,
               detail_fail="'service password-encryption' not found"),
        _check(config, "ntp-configured",
               r"^ntp server\s+\S+", expect_match=True,
               detail_fail="No 'ntp server' statement found"),
        _check(config, "no-snmp-rw-community",
               r"^snmp-server community \S+ RW", expect_match=False,
               detail_fail="SNMP read-write community string present"),
        _check(config, "logging-host",
               r"^logging host\s+\S+", expect_match=True,
               detail_fail="No 'logging host' configured"),
        _check(config, "console-timeout",
               r"(?s)line con 0.*?exec-timeout\s+[1-9]", expect_match=True,
               detail_fail="console exec-timeout is 0 or missing"),
        _check(config, "vty-timeout",
               r"(?s)line vty.*?exec-timeout\s+[1-9]", expect_match=True,
               detail_fail="VTY exec-timeout is 0 or missing"),
    ]

    report = HostReport(hostname=task.host.name, checks=checks)
    task.host.data["compliance_report"] = report

    summary = f"{'PASS' if report.passed else 'FAIL'} ({report.failure_count} failures)"
    return Result(host=task.host, result=summary, failed=not report.passed)


def print_summary(reports: List[HostReport]) -> None:
    print("\n" + "=" * 60)
    print(f"{'HOST':<30} {'STATUS':<8} FAILURES")
    print("-" * 60)
    for r in reports:
        status = "PASS" if r.passed else "FAIL"
        failures = "; ".join(c.name for c in r.checks if not c.passed) or "—"
        print(f"{r.hostname:<30} {status:<8} {failures}")
    total = len(reports)
    passed = sum(1 for r in reports if r.passed)
    print("=" * 60)
    print(f"Result: {passed}/{total} hosts compliant\n")


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run security compliance audit against network devices"
    )
    parser.add_argument("--inventory", default="inventory/",
                        help="Path to Nornir inventory directory")
    parser.add_argument("--host", metavar="HOSTNAME",
                        help="Audit a single host by name")
    parser.add_argument("--groups", metavar="GRP1,GRP2",
                        help="Comma-separated list of Nornir groups to target")
    parser.add_argument("--output", metavar="FILE",
                        help="Write JSON report to FILE")
    parser.add_argument("--fail-fast", action="store_true",
                        help="Exit with code 1 if any host fails")
    parser.add_argument("--workers", type=int, default=10,
                        help="Nornir runner thread count (default: 10)")
    parser.add_argument("--verbose", action="store_true",
                        help="Log debug output")
    return parser.parse_args()


def main() -> None:
    args = build_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": f"{args.inventory}/hosts.yaml",
                "group_file": f"{args.inventory}/groups.yaml",
                "defaults_file": f"{args.inventory}/defaults.yaml",
            },
        },
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
    )

    if args.host:
        nr = nr.filter(name=args.host)
    elif args.groups:
        for group in args.groups.split(","):
            nr = nr.filter(F(groups__contains=group.strip()))

    if not nr.inventory.hosts:
        log.error("No hosts matched the filter — check --host / --groups")
        sys.exit(2)

    log.info("Auditing %d host(s)...", len(nr.inventory.hosts))
    nr.run(task=run_compliance_checks, name="compliance_audit")

    reports: List[HostReport] = [
        nr.inventory.hosts[h].data["compliance_report"]
        for h in nr.inventory.hosts
        if "compliance_report" in nr.inventory.hosts[h].data
    ]

    print_summary(reports)

    if args.output:
        payload = [asdict(r) for r in reports]
        with open(args.output, "w") as fh:
            json.dump(payload, fh, indent=2)
        log.info("JSON report written to %s", args.output)

    if args.fail_fast and any(not r.passed for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    main()
```