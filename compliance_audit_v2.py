```
"""
Management Plane Compliance Audit

Purpose:
    Audits network devices for management plane security compliance, verifying
    NTP server configuration, syslog destinations, MOTD banner presence,
    SSH version enforcement, and AAA/TACACS authentication setup against a
    policy baseline supplied at the command line.

    This complements general compliance_audit.py (which focuses on interface
    and routing policy) by targeting the out-of-band management control plane.

Usage:
    python 034_compliance_audit.py \
        --host 192.168.1.1 --username admin --password secret \
        --platform cisco_ios \
        --ntp-servers 10.0.0.1 10.0.0.2 \
        --syslog-servers 10.0.0.10 \
        --require-banner --require-ssh2 --require-aaa

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
"""

import argparse
import logging
import sys

from nornir.core import Nornir
from nornir.core.configuration import Config
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.plugins.runners import ThreadedRunner
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

COMMANDS = {
    "ntp": "show ntp associations",
    "syslog": "show logging",
    "banner": "show banner motd",
    "ssh": "show ip ssh",
    "aaa": "show aaa servers",
}


def _check_servers(output: str, required: list) -> dict:
    missing = [s for s in required if s not in output]
    return {
        "compliant": not missing,
        "detail": f"Missing: {missing}" if missing else "All required servers present",
    }


def _check_banner(output: str) -> dict:
    present = bool(output.strip()) and len(output.strip()) > 5
    return {
        "compliant": present,
        "detail": "MOTD banner configured" if present else "No MOTD banner found",
    }


def _check_ssh(output: str) -> dict:
    ok = "SSH Enabled - version 2.0" in output or "version 2.0" in output.lower()
    return {
        "compliant": ok,
        "detail": "SSH v2.0 enforced" if ok else "SSH v2.0 not confirmed — check 'ip ssh version 2'",
    }


def _check_aaa(output: str) -> dict:
    ok = any(kw in output.upper() for kw in ("TACACS", "RADIUS")) or len(output.strip()) > 30
    return {
        "compliant": ok,
        "detail": "AAA servers configured" if ok else "No AAA server entries found",
    }


def mgmt_plane_audit(task: Task, policy: dict) -> Result:
    checks = {}
    errors = []

    for key, cmd in COMMANDS.items():
        try:
            r = task.run(
                task=netmiko_send_command,
                command_string=cmd,
                name=f"cmd_{key}",
            )
            output = r.result or ""
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            output = ""

        if key == "ntp" and policy["ntp_servers"]:
            checks["ntp_servers"] = _check_servers(output, policy["ntp_servers"])
        elif key == "syslog" and policy["syslog_servers"]:
            checks["syslog_servers"] = _check_servers(output, policy["syslog_servers"])
        elif key == "banner" and policy["require_banner"]:
            checks["motd_banner"] = _check_banner(output)
        elif key == "ssh" and policy["require_ssh2"]:
            checks["ssh_version"] = _check_ssh(output)
        elif key == "aaa" and policy["require_aaa"]:
            checks["aaa_servers"] = _check_aaa(output)

    if not checks and not errors:
        errors.append("No policy checks enabled — pass at least one --require-* flag or --ntp/--syslog-servers")

    overall = bool(checks) and all(v["compliant"] for v in checks.values())
    return Result(
        host=task.host,
        result={"checks": checks, "compliant": overall, "errors": errors},
    )


def print_report(agg_result) -> int:
    total = len(agg_result)
    noncompliant = 0

    for host, multi_result in agg_result.items():
        if multi_result.failed:
            print(f"\n[ERROR] {host}: {multi_result.exception}")
            noncompliant += 1
            continue

        data = multi_result[0].result
        status = "COMPLIANT" if data["compliant"] else "NON-COMPLIANT"
        bar = "=" * 58
        print(f"\n{bar}")
        print(f"  Host : {host}")
        print(f"  Status: {status}")
        print(bar)

        for check, info in data["checks"].items():
            mark = "PASS" if info["compliant"] else "FAIL"
            print(f"  [{mark}] {check:<18} {info['detail']}")

        for err in data["errors"]:
            print(f"  [WARN] {err}")

        if not data["compliant"]:
            noncompliant += 1

    print(f"\nResult: {total - noncompliant}/{total} hosts compliant\n")
    return 1 if noncompliant else 0


def build_nornir(args: argparse.Namespace) -> Nornir:
    defaults = Defaults(username=args.username, password=args.password)
    host = Host(
        name=args.host,
        hostname=args.host,
        port=args.port,
        platform=args.platform,
        defaults=defaults,
    )
    inventory = Inventory(
        hosts=Hosts({args.host: host}),
        groups=Groups(),
        defaults=defaults,
    )
    return Nornir(
        inventory=inventory,
        runner=ThreadedRunner(num_workers=1),
        config=Config(),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Management plane compliance audit (NTP, syslog, banner, SSH, AAA)"
    )
    p.add_argument("--host", required=True, help="Device hostname or IP")
    p.add_argument("--username", required=True, help="SSH username")
    p.add_argument("--password", required=True, help="SSH password")
    p.add_argument("--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)")
    p.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    p.add_argument("--ntp-servers", nargs="*", default=[], metavar="IP",
                   help="Required NTP server IPs")
    p.add_argument("--syslog-servers", nargs="*", default=[], metavar="IP",
                   help="Required syslog server IPs")
    p.add_argument("--require-banner", action="store_true",
                   help="Fail if no MOTD banner is configured")
    p.add_argument("--require-ssh2", action="store_true",
                   help="Fail if SSH version 2 is not enforced")
    p.add_argument("--require-aaa", action="store_true",
                   help="Fail if no AAA/TACACS/RADIUS servers are configured")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    policy = {
        "ntp_servers": args.ntp_servers,
        "syslog_servers": args.syslog_servers,
        "require_banner": args.require_banner,
        "require_ssh2": args.require_ssh2,
        "require_aaa": args.require_aaa,
    }

    nr = build_nornir(args)

    result = nr.run(task=mgmt_plane_audit, policy=policy)
    sys.exit(print_report(result))
```