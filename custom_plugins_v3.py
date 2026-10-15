NTP Synchronization Audit — nornir-network-automation portfolio script.

Connects to network devices via Nornir/Netmiko, collects NTP status and
association data, and reports synchronization health across the fleet.
Flags devices that are unsynchronized, exceed a stratum threshold, or
have an offset outside acceptable bounds.

Usage:
    python ntp_audit.py --hosts router1,router2 --username admin --password secret
    python ntp_audit.py --hosts core-rtr1 --max-stratum 3 --max-offset 50 --output ntp.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils

Supported platforms: Cisco IOS, IOS-XE, IOS-XR, NX-OS
(parses output of: show ntp status / show ntp associations).
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

from nornir import InitNornir
from nornir.core.inventory import ConnectionOptions, Defaults, Host
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class NtpStatus:
    hostname: str
    synchronized: bool = False
    stratum: Optional[int] = None
    reference: Optional[str] = None
    offset_ms: Optional[float] = None
    peer_count: int = 0
    errors: list = field(default_factory=list)


def _parse_ntp_status(raw: str, hostname: str) -> NtpStatus:
    status = NtpStatus(hostname=hostname)

    sync_match = re.search(r"Clock is (synchronized|unsynchronized)", raw, re.I)
    if sync_match:
        status.synchronized = sync_match.group(1).lower() == "synchronized"
    else:
        status.errors.append("could not determine sync state")

    stratum_match = re.search(r"stratum\s+(\d+)", raw, re.I)
    if stratum_match:
        status.stratum = int(stratum_match.group(1))

    ref_match = re.search(r"reference is\s+([\d.a-zA-Z.-]+)", raw, re.I)
    if ref_match:
        status.reference = ref_match.group(1)

    offset_match = re.search(r"offset\s+is\s+([\-\d.]+)\s+msec", raw, re.I)
    if offset_match:
        status.offset_ms = float(offset_match.group(1))

    return status


def collect_ntp(task: Task) -> Result:
    status_raw = task.run(
        task=netmiko_send_command,
        command_string="show ntp status",
        name="ntp_status",
    )
    assoc_raw = task.run(
        task=netmiko_send_command,
        command_string="show ntp associations",
        name="ntp_associations",
    )

    ntp = _parse_ntp_status(status_raw.result, task.host.name)

    peer_lines = [
        ln for ln in assoc_raw.result.splitlines()
        if re.match(r"\s*[~*#+\-]?\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", ln)
    ]
    ntp.peer_count = len(peer_lines)

    return Result(host=task.host, result=asdict(ntp))


def build_nornir(hosts: list, username: str, password: str, platform: str, port: int):
    host_dict = {}
    for h in hosts:
        host_dict[h] = Host(
            name=h,
            hostname=h,
            username=username,
            password=password,
            platform=platform,
            port=port,
            connection_options={
                "netmiko": ConnectionOptions(extras={"timeout": 30})
            },
        )
    defaults = Defaults(username=username, password=password, platform=platform)
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 10}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {"host_file": None, "group_file": None, "defaults_file": None},
        },
        logging={"enabled": False},
    )
    nr.inventory.hosts = host_dict
    nr.inventory.defaults = defaults
    return nr


def print_report(results: list, max_stratum: int, max_offset: float) -> int:
    failures = 0
    header = f"\n{'HOST':<22} {'SYNC':<6} {'STRATUM':<9} {'OFFSET(ms)':<13} {'PEERS':<7} STATUS"
    print(header)
    print("-" * 75)

    for r in results:
        sync = "YES" if r["synchronized"] else "NO"
        stratum = str(r["stratum"]) if r["stratum"] is not None else "?"
        offset = f"{r['offset_ms']:.2f}" if r["offset_ms"] is not None else "?"
        peers = str(r["peer_count"])

        issues = list(r["errors"])
        if not r["synchronized"]:
            issues.append("not synced")
        if r["stratum"] is not None and r["stratum"] > max_stratum:
            issues.append(f"stratum {r['stratum']} > {max_stratum}")
        if r["offset_ms"] is not None and abs(r["offset_ms"]) > max_offset:
            issues.append(f"offset {r['offset_ms']:.1f}ms > ±{max_offset}ms")

        status = "FAIL: " + "; ".join(issues) if issues else "OK"
        if issues:
            failures += 1

        print(f"{r['hostname']:<22} {sync:<6} {stratum:<9} {offset:<13} {peers:<7} {status}")

    total = len(results)
    print(f"\nSummary: {total} device(s) checked, {failures} with issues, {total - failures} clean.\n")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit NTP synchronization health across network devices."
    )
    parser.add_argument(
        "--hosts", required=True,
        help="Comma-separated hostnames or IPs to audit",
    )
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--platform", default="cisco_ios",
        help="Netmiko platform string (default: cisco_ios)",
    )
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument(
        "--max-stratum", type=int, default=5,
        help="Flag devices with stratum above this value (default: 5)",
    )
    parser.add_argument(
        "--max-offset", type=float, default=100.0,
        help="Flag devices with |offset| above this ms (default: 100)",
    )
    parser.add_argument("--output", help="Write JSON results to this file path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]
    if not host_list:
        parser.error("--hosts must contain at least one hostname.")

    logger.info("Starting NTP audit for %d host(s)", len(host_list))
    nr = build_nornir(host_list, args.username, args.password, args.platform, args.port)

    agg = nr.run(task=collect_ntp, name="NTP Audit")

    results = []
    for host, multi in agg.items():
        if multi.failed:
            err = str(multi.exception) if multi.exception else "task failed"
            results.append(asdict(NtpStatus(hostname=host, errors=[err])))
            logger.error("Host %s failed: %s", host, err)
        else:
            results.append(multi.result)

    failures = print_report(results, args.max_stratum, args.max_offset)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"Results written to {args.output}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()