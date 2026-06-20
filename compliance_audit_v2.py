The target repo isn't at `/opt/NetAutoCommitter` — the user wants the script content output directly. Here it is:

```
"""
NTP Compliance Audit — nornir-network-automation

Audits NTP configuration and synchronization status across network devices.
Checks that required NTP servers are configured, the device clock is
synchronized, and optionally that NTP authentication keys are present.

Usage:
    python ntp_compliance_audit.py \
        --inventory inventory/hosts.yaml \
        --ntp-servers 10.0.0.1 10.0.0.2 \
        --require-auth \
        --filter-site dc1

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files in SimpleInventory format (hosts.yaml, groups.yaml, defaults.yaml)

Exit codes: 0 = all devices compliant, 1 = one or more failures or errors.
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import List, Tuple

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@dataclass
class NtpAudit:
    hostname: str
    configured_servers: List[str] = field(default_factory=list)
    synchronized: bool = False
    auth_configured: bool = False
    missing_servers: List[str] = field(default_factory=list)
    compliant: bool = False
    error: str = ""


def _parse_associations(output: str) -> Tuple[List[str], bool]:
    """Parse 'show ntp associations' into (server_ips, is_synchronized)."""
    servers: List[str] = []
    synchronized = False

    for line in output.splitlines():
        raw = line.strip()
        if not raw or raw.lower().startswith("address"):
            continue

        synced_candidate = raw.startswith("*")

        cleaned = raw.lstrip("*+-~ ")
        if cleaned.startswith("~"):
            cleaned = cleaned[1:].strip()

        parts = cleaned.split()
        if not parts:
            continue
        ip = parts[0]

        octets = ip.split(".")
        if len(octets) == 4 and all(o.isdigit() for o in octets):
            servers.append(ip)
            if synced_candidate:
                synchronized = True

    return servers, synchronized


def audit_ntp(task: Task, required_servers: List[str], require_auth: bool) -> Result:
    """Nornir task: collect NTP data and evaluate per-device compliance."""
    audit = NtpAudit(hostname=task.host.name)

    try:
        assoc_r = task.run(
            task=netmiko_send_command,
            command_string="show ntp associations",
            name="ntp-associations",
        )
        status_r = task.run(
            task=netmiko_send_command,
            command_string="show ntp status",
            name="ntp-status",
        )
        cfg_r = task.run(
            task=netmiko_send_command,
            command_string="show running-config | include ntp",
            name="ntp-config",
        )
    except Exception as exc:
        audit.error = str(exc)
        log.error("%s: connection failed — %s", task.host.name, exc)
        return Result(host=task.host, result=audit)

    servers, synced = _parse_associations(assoc_r.result)
    audit.configured_servers = servers
    audit.synchronized = synced or "synchronized" in status_r.result.lower()

    cfg_lower = cfg_r.result.lower()
    audit.auth_configured = "ntp authentication-key" in cfg_lower

    audit.missing_servers = [s for s in required_servers if s not in servers]

    auth_pass = (not require_auth) or audit.auth_configured
    audit.compliant = (
        len(audit.missing_servers) == 0 and audit.synchronized and auth_pass
    )

    return Result(host=task.host, result=audit)


def _failure_reasons(audit: NtpAudit, require_auth: bool) -> List[str]:
    reasons: List[str] = []
    if audit.error:
        return [f"error: {audit.error}"]
    if audit.missing_servers:
        reasons.append(f"missing servers: {', '.join(audit.missing_servers)}")
    if not audit.synchronized:
        reasons.append("clock not synchronized")
    if require_auth and not audit.auth_configured:
        reasons.append("NTP authentication not configured")
    return reasons or ["unknown"]


def print_report(
    results: dict, required_servers: List[str], require_auth: bool
) -> int:
    """Print compliance report; return number of non-compliant/errored devices."""
    passed: List[NtpAudit] = []
    failed: List[NtpAudit] = []

    for hostname in results:
        audit: NtpAudit = results[hostname].result
        (passed if audit.compliant else failed).append(audit)

    sep = "=" * 62
    print(f"\n{sep}")
    print("  NTP COMPLIANCE AUDIT REPORT")
    print(f"  Required NTP servers : {', '.join(required_servers) or '(any)'}")
    print(f"  Require authentication: {'yes' if require_auth else 'no'}")
    print(sep)

    for audit in sorted(passed, key=lambda a: a.hostname):
        print(f"  PASS  {audit.hostname}")
        if audit.configured_servers:
            print(f"        servers: {', '.join(audit.configured_servers)}")

    for audit in sorted(failed, key=lambda a: a.hostname):
        reasons = _failure_reasons(audit, require_auth)
        print(f"  FAIL  {audit.hostname}")
        for reason in reasons:
            print(f"        - {reason}")

    total = len(passed) + len(failed)
    print(sep)
    print(
        f"  Result: {len(passed)} passed, {len(failed)} failed"
        f" out of {total} device(s)\n"
    )

    return len(failed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit NTP configuration and sync status across network devices"
    )
    parser.add_argument(
        "--inventory", default="inventory/hosts.yaml", help="Path to hosts.yaml"
    )
    parser.add_argument(
        "--groups", default="inventory/groups.yaml", help="Path to groups.yaml"
    )
    parser.add_argument(
        "--defaults", default="inventory/defaults.yaml", help="Path to defaults.yaml"
    )
    parser.add_argument(
        "--ntp-servers",
        nargs="*",
        default=[],
        metavar="IP",
        help="Required NTP server IPs (space-separated); omit to only check sync",
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="Fail devices with no NTP authentication-key in running config",
    )
    parser.add_argument(
        "--filter-site",
        metavar="SITE",
        help="Restrict audit to hosts where site= matches this value",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Concurrent device connections (default: 10)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.inventory,
                "group_file": args.groups,
                "defaults_file": args.defaults,
            },
        },
    )

    if args.filter_site:
        nr = nr.filter(site=args.filter_site)

    log.info("Starting NTP audit on %d device(s)", len(nr.inventory.hosts))

    nornir_results = nr.run(
        task=audit_ntp,
        required_servers=args.ntp_servers,
        require_auth=args.require_auth,
    )

    failures = print_report(nornir_results, args.ntp_servers, args.require_auth)
    sys.exit(1 if failures else 0)
```