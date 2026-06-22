The write was blocked by permissions. Here is the complete script content:

```python
"""
Management Plane Security Compliance Audit
==========================================
Audits network devices for management plane hardening controls:
SSH version, AAA authentication, NTP configuration, SNMP security,
service password-encryption, and VTY line login requirements.

Usage:
    python mgmt_plane_audit.py --host 10.0.0.1 --username admin --password secret
    python mgmt_plane_audit.py --inventory hosts.yaml --groups routers --output report.json
    python mgmt_plane_audit.py --inventory hosts.yaml --fail-only

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Devices must accept SSH; IOS/IOS-XE show commands are expected.
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional

from nornir import InitNornir
from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

CHECKS = {
    "ssh_v2": ("show ip ssh", "SSH Version 2"),
    "aaa_new_model": ("show running-config | include aaa new-model", "aaa new-model"),
    "ntp_server": ("show ntp associations", "configured"),
    "password_encryption": ("show running-config | include service password-encryption", "service password-encryption"),
    "snmp_v3": ("show snmp group", "priv"),
    "vty_login": ("show running-config | section line vty", "login"),
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class DeviceReport:
    hostname: str
    checks: list = field(default_factory=list)

    @property
    def passed(self):
        return all(c["passed"] for c in self.checks)

    @property
    def score(self):
        total = len(self.checks)
        return f"{sum(1 for c in self.checks if c['passed'])}/{total}" if total else "0/0"


def _check_output(output: str, marker: str) -> bool:
    return marker.lower() in output.lower()


def audit_device(task: Task) -> Result:
    results = []
    for check_name, (command, marker) in CHECKS.items():
        try:
            r = task.run(task=netmiko_send_command, command_string=command, severity_level=logging.DEBUG)
            output = r.result or ""
            passed = _check_output(output, marker)
            results.append(asdict(CheckResult(name=check_name, passed=passed, detail=output.strip()[:120])))
        except Exception as exc:
            results.append(asdict(CheckResult(name=check_name, passed=False, detail=f"ERROR: {exc}")))

    report = DeviceReport(hostname=task.host.name, checks=results)
    return Result(host=task.host, result=report)


def build_inventory(host: str, username: str, password: str, platform: str) -> Inventory:
    h = Host(
        name=host,
        hostname=host,
        username=username,
        password=password,
        platform=platform,
        connection_options={"netmiko": ConnectionOptions(extras={"device_type": platform})},
    )
    return Inventory(hosts=Hosts({host: h}), groups=Groups(), defaults=Defaults())


def run_audit(nr, fail_only: bool) -> list:
    results = nr.run(task=audit_device, name="mgmt_plane_audit")
    reports = []
    for hostname, mr in results.items():
        if mr.failed:
            logger.error("Failed to audit %s: %s", hostname, mr.exception)
            continue
        report: DeviceReport = mr.result
        if fail_only and report.passed:
            continue
        reports.append(report)
    return reports


def print_reports(reports: list, fail_only: bool):
    for report in reports:
        status = "PASS" if report.passed else "FAIL"
        print(f"\n{'='*60}")
        print(f"Device: {report.hostname}  [{status}]  Score: {report.score}")
        print(f"{'='*60}")
        for check in report.checks:
            mark = "+" if check["passed"] else "-"
            print(f"  [{mark}] {check['name']}")
            if not check["passed"]:
                snippet = check["detail"][:80].replace("\n", " ")
                if snippet:
                    print(f"       ^ {snippet}")


def main():
    parser = argparse.ArgumentParser(
        description="Audit network device management plane security controls",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--host", help="Single device hostname or IP")
    src.add_argument("--inventory", help="Nornir inventory config file (YAML)")
    parser.add_argument("--username", help="SSH username")
    parser.add_argument("--password", help="SSH password")
    parser.add_argument("--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)")
    parser.add_argument("--groups", nargs="+", help="Limit to inventory groups")
    parser.add_argument("--output", help="Write JSON report to file")
    parser.add_argument("--fail-only", action="store_true", help="Only show/report devices with failures")
    args = parser.parse_args()

    if args.host:
        if not args.username or not args.password:
            parser.error("--username and --password are required with --host")
        nr = InitNornir(inventory={"plugin": "SimpleInventory"})
        nr.inventory = build_inventory(args.host, args.username, args.password, args.platform)
    else:
        nr = InitNornir(config_file=args.inventory)
        if args.groups:
            nr = nr.filter(lambda h: any(g in h.groups for g in args.groups))

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    reports = run_audit(nr, args.fail_only)

    if not reports:
        print("No devices to report." if args.fail_only else "No devices audited.")
        sys.exit(0)

    print_reports(reports, args.fail_only)

    if args.output:
        payload = [{"hostname": r.hostname, "passed": r.passed, "score": r.score, "checks": r.checks} for r in reports]
        with open(args.output, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nReport written to {args.output}")

    any_failures = any(not r.passed for r in reports)
    sys.exit(1 if any_failures else 0)


if __name__ == "__main__":
    main()
```

This is `mgmt_plane_audit.py` — a management plane security compliance auditor that checks six hardening controls (SSH v2, AAA, NTP, service password-encryption, SNMPv3, VTY login) and exits non-zero if any device fails, making it CI-friendly. It supports both single-host mode and full Nornir inventory files with group filtering and JSON report output.