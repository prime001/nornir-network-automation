Device Hardening Compliance Audit — CIS Cisco IOS Benchmark checks.

Audits network devices against common security hardening controls:
  - SSH v2 enforced (no telnet)
  - Service password-encryption enabled
  - Login banner (MOTD) configured
  - VTY exec-timeout set
  - HTTP server disabled
  - Telnet not permitted on VTY lines

Usage:
    python compliance_audit_v3.py --host 192.168.1.1 --username admin --password secret
    python compliance_audit_v3.py --inventory nornir_config.yaml --groups routers
    python compliance_audit_v3.py --host 10.0.0.1 -u admin -p secret --output report.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Devices must accept SSH; Cisco IOS/IOS-XE assumed.
"""

import argparse
import json
import logging
import sys

from nornir import InitNornir
from nornir.core.inventory import Defaults, Host
from nornir.core.task import Result, Task
from nornir_netmiko import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("hardening_audit")

CHECKS = {
    "ssh_v2": {
        "command": "show ip ssh",
        "pass_pattern": "SSH Enabled - version 2",
        "description": "SSH v2 enforced",
    },
    "password_encryption": {
        "command": "show running-config | include service password-encryption",
        "pass_pattern": "service password-encryption",
        "description": "Password encryption service enabled",
    },
    "motd_banner": {
        "command": "show running-config | include banner motd",
        "pass_pattern": "banner motd",
        "description": "MOTD login banner configured",
    },
    "no_http_server": {
        "command": "show running-config | include ^ip http server",
        "fail_pattern": "ip http server",
        "description": "HTTP server disabled",
    },
    "vty_timeout": {
        "command": "show running-config | section line vty",
        "pass_pattern": "exec-timeout",
        "description": "VTY exec-timeout configured",
    },
    "no_telnet": {
        "command": "show running-config | section line vty",
        "fail_pattern": "transport input telnet",
        "description": "Telnet not permitted on VTY lines",
    },
}


def run_hardening_checks(task: Task, fail_fast: bool = False) -> Result:
    results = {}
    passed = 0
    failed = 0

    for check_id, check in CHECKS.items():
        try:
            r = task.run(
                task=netmiko_send_command,
                command_string=check["command"],
                name=check_id,
            )
            output = r.result or ""
        except Exception as exc:
            logger.warning("%s: check %s error: %s", task.host.name, check_id, exc)
            results[check_id] = {
                "description": check["description"],
                "status": "ERROR",
                "detail": str(exc),
            }
            failed += 1
            if fail_fast:
                break
            continue

        if "pass_pattern" in check:
            status = "PASS" if check["pass_pattern"] in output else "FAIL"
        else:
            status = "FAIL" if check.get("fail_pattern", "") in output else "PASS"

        results[check_id] = {"description": check["description"], "status": status}
        if status == "PASS":
            passed += 1
        else:
            failed += 1
            if fail_fast:
                break

    summary = {"passed": passed, "failed": failed, "checks": results}
    return Result(host=task.host, result=summary, failed=failed > 0)


def build_nornir_single(host: str, username: str, password: str):
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {"hosts_file": None, "groups_file": None, "defaults_file": None},
        },
        logging={"enabled": False},
    )
    nr.inventory.hosts.clear()
    defaults = Defaults(username=username, password=password)
    nr.inventory.hosts[host] = Host(
        name=host,
        hostname=host,
        username=username,
        password=password,
        platform="cisco_ios",
        defaults=defaults,
    )
    return nr


def build_nornir_inventory(config_file: str, groups: list, workers: int):
    nr = InitNornir(
        config_file=config_file,
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        logging={"enabled": False},
    )
    if groups:
        nr = nr.filter(lambda h: any(g in [str(x) for x in h.groups] for g in groups))
    return nr


def print_report(results, output_file):
    report = {}
    for host, result in results.items():
        if result.exception:
            report[host] = {"error": str(result.exception)}
        else:
            report[host] = result.result

    payload = json.dumps(report, indent=2)
    if output_file:
        with open(output_file, "w") as f:
            f.write(payload)
        print(f"Report written to {output_file}", file=sys.stderr)
    else:
        print(payload)

    total = len(report)
    compliant = sum(
        1 for v in report.values()
        if isinstance(v, dict) and v.get("failed", 1) == 0
    )
    print(f"\nResult: {compliant}/{total} hosts fully compliant", file=sys.stderr)


def parse_args():
    parser = argparse.ArgumentParser(
        description="CIS hardening compliance audit via Nornir/Netmiko"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", help="Single device IP or hostname")
    target.add_argument("--inventory", help="Nornir YAML config file")
    parser.add_argument("-u", "--username", default="admin", help="SSH username")
    parser.add_argument("-p", "--password", default="", help="SSH password")
    parser.add_argument("--groups", nargs="+", help="Filter to these inventory groups")
    parser.add_argument("--output", help="Write JSON report to this file")
    parser.add_argument(
        "--fail-fast", action="store_true",
        help="Stop checking a device after first failed control"
    )
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Concurrent device workers (default: 10)"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        if args.host:
            nr = build_nornir_single(args.host, args.username, args.password)
        else:
            nr = build_nornir_inventory(args.inventory, args.groups or [], args.workers)
    except Exception as exc:
        logger.error("Inventory init failed: %s", exc)
        sys.exit(1)

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check inventory or --groups filter")
        sys.exit(1)

    results = nr.run(
        task=run_hardening_checks,
        name="hardening_audit",
        fail_fast=args.fail_fast,
    )

    print_report(results, args.output)

    failed_hosts = [h for h, r in results.items() if r.failed]
    if failed_hosts:
        print(f"Non-compliant hosts: {', '.join(failed_hosts)}", file=sys.stderr)
        sys.exit(1)