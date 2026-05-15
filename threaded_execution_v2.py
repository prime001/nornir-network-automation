The existing scripts confirmed - no NTP audit exists. Writing now.

```python
"""
NTP Synchronization Audit — nornir-network-automation

Queries NTP status across all inventory devices and reports sync state,
stratum, reference server, and clock offset. Flags devices that are
unsynchronized, have excessive offset, or reference an unknown NTP peer.

Usage:
    python ntp_audit.py
    python ntp_audit.py --group datacenter --max-offset 50
    python ntp_audit.py --username admin --password secret --output ntp.json
    python ntp_audit.py --expected-server 10.0.0.1 --workers 20

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory: inventory/hosts.yaml, inventory/groups.yaml, inventory/defaults.yaml
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

NTP_COMMANDS = {
    "cisco_ios": "show ntp status",
    "cisco_nxos": "show ntp status",
    "cisco_xr": "show ntp status",
    "arista_eos": "show ntp status",
    "juniper_junos": "show ntp status",
}

SYNC_PATTERN = re.compile(
    r"(Clock is synchronized|synchronized,|Clock is unsynchronized|unsynchronized)",
    re.IGNORECASE,
)
STRATUM_PATTERN = re.compile(r"stratum\s+(\d+)", re.IGNORECASE)
REFERENCE_PATTERN = re.compile(
    r"reference\s+(?:is\s+)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|[\w.-]+)",
    re.IGNORECASE,
)
OFFSET_PATTERN = re.compile(r"offset\s+(?:is\s+)?([-\d.]+)", re.IGNORECASE)


def parse_ntp_status(raw: str) -> dict:
    synchronized = False
    if SYNC_PATTERN.search(raw):
        synchronized = "unsynchronized" not in SYNC_PATTERN.search(raw).group(0).lower()

    stratum_match = STRATUM_PATTERN.search(raw)
    ref_match = REFERENCE_PATTERN.search(raw)
    offset_match = OFFSET_PATTERN.search(raw)

    return {
        "synchronized": synchronized,
        "stratum": int(stratum_match.group(1)) if stratum_match else None,
        "reference": ref_match.group(1) if ref_match else None,
        "offset_ms": float(offset_match.group(1)) if offset_match else None,
        "raw": raw.strip(),
    }


def audit_ntp(task: Task, max_offset: float, expected_server: str | None) -> Result:
    platform = task.host.platform or "cisco_ios"
    command = NTP_COMMANDS.get(platform, "show ntp status")

    cmd_result = task.run(
        task=netmiko_send_command,
        command_string=command,
        name=f"NTP status from {task.host.name}",
    )

    parsed = parse_ntp_status(cmd_result.result)

    violations = []
    if not parsed["synchronized"]:
        violations.append("NOT_SYNCHRONIZED")
    if parsed["stratum"] and parsed["stratum"] >= 16:
        violations.append(f"STRATUM_INVALID:{parsed['stratum']}")
    if parsed["offset_ms"] is not None and abs(parsed["offset_ms"]) > max_offset:
        violations.append(f"OFFSET_EXCEEDED:{parsed['offset_ms']}ms")
    if expected_server and parsed["reference"] and parsed["reference"] != expected_server:
        violations.append(f"UNEXPECTED_REF:{parsed['reference']}")

    parsed["violations"] = violations
    parsed["compliant"] = len(violations) == 0
    parsed["host"] = task.host.name

    return Result(host=task.host, result=parsed)


def print_report(results: list[dict], max_offset: float, expected_server: str | None) -> None:
    compliant = [r for r in results if r["compliant"]]
    non_compliant = [r for r in results if not r["compliant"]]

    print(f"\n{'='*64}")
    print(f"NTP Synchronization Audit — {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"{'='*64}")
    print(f"Devices audited : {len(results)}")
    print(f"Compliant       : {len(compliant)}")
    print(f"Non-compliant   : {len(non_compliant)}")
    print(f"Max offset (ms) : {max_offset}")
    if expected_server:
        print(f"Expected server : {expected_server}")

    if non_compliant:
        print(f"\n{'--- VIOLATIONS ':{'─'}<50}")
        for r in non_compliant:
            print(f"  {r['host']:<30} {', '.join(r['violations'])}")

    print(f"\n{'--- DEVICE SUMMARY ':{'─'}<50}")
    for r in sorted(results, key=lambda x: x["host"]):
        sync_str = "SYNC" if r["synchronized"] else "UNSYNC"
        stratum = str(r["stratum"]) if r["stratum"] is not None else "?"
        ref = r["reference"] or "unknown"
        offset = f"{r['offset_ms']:+.2f}ms" if r["offset_ms"] is not None else "?"
        status = "OK" if r["compliant"] else "FAIL"
        print(f"  [{status}] {r['host']:<28} {sync_str} str={stratum} ref={ref} offset={offset}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit NTP synchronization status across network devices"
    )
    parser.add_argument("--config", default="config.yaml", help="Nornir config file")
    parser.add_argument("--group", help="Filter to this inventory group")
    parser.add_argument("--username", help="Override inventory username")
    parser.add_argument("--password", help="Override inventory password")
    parser.add_argument(
        "--max-offset",
        dest="max_offset",
        type=float,
        default=100.0,
        help="Maximum acceptable clock offset in milliseconds (default: 100)",
    )
    parser.add_argument(
        "--expected-server",
        dest="expected_server",
        help="Flag devices not referencing this NTP server IP",
    )
    parser.add_argument("--workers", type=int, default=10, help="Parallel worker threads")
    parser.add_argument("--output", help="Write JSON report to this file path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(config_file=args.config, core={"num_workers": args.workers})

    if args.group:
        nr = nr.filter(filter_func=lambda h: args.group in h.groups)
    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if not nr.inventory.hosts:
        logger.error("No hosts matched. Check --group or inventory configuration.")
        sys.exit(1)

    logger.info("Auditing NTP on %d hosts...", len(nr.inventory.hosts))

    agg = nr.run(
        task=audit_ntp,
        max_offset=args.max_offset,
        expected_server=args.expected_server,
        name="NTP audit",
    )

    parsed_results = []
    for host, multi_result in agg.items():
        if multi_result.failed:
            logger.warning("Failed to audit %s: %s", host, multi_result[0].exception)
            parsed_results.append({
                "host": host,
                "synchronized": False,
                "compliant": False,
                "violations": ["CONNECTION_FAILED"],
                "stratum": None,
                "reference": None,
                "offset_ms": None,
                "raw": "",
            })
        else:
            parsed_results.append(multi_result[0].result)

    print_report(parsed_results, args.max_offset, args.expected_server)

    non_compliant_count = sum(1 for r in parsed_results if not r["compliant"])

    if args.output:
        report = {
            "generated": datetime.utcnow().isoformat() + "Z",
            "max_offset_ms": args.max_offset,
            "expected_server": args.expected_server,
            "summary": {
                "total": len(parsed_results),
                "compliant": len(parsed_results) - non_compliant_count,
                "non_compliant": non_compliant_count,
            },
            "devices": parsed_results,
        }
        with open(args.output, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        logger.info("Report written to %s", args.output)

    sys.exit(1 if non_compliant_count else 0)


if __name__ == "__main__":
    main()
```