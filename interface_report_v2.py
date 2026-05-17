The existing `interface_report.py` covers status/IP/protocol, and `interface_report_v2.py` is actually a route validator. I'll write an interface **error counter** script — collects CRC/drop/reset counters, flags interfaces exceeding thresholds.

```python
"""
Interface Error Counter Report

Collects per-interface error counters (input errors, CRC, drops, resets,
runts, giants) from network devices via Nornir and flags interfaces that
exceed configurable thresholds. Useful for identifying physical-layer
problems and congestion points before they cause outages.

Usage:
    python interface_errors.py --config config.yaml
    python interface_errors.py --devices core-1,core-2 --threshold 100
    python interface_errors.py --format json --min-errors 1 > errors.json

Prerequisites:
    - Nornir inventory configured (config.yaml, hosts.yaml, groups.yaml)
    - Device credentials available via inventory or environment variables
    - nornir-netmiko installed: pip install nornir-netmiko
    - Supported platforms: Cisco IOS/IOS-XE, NX-OS, Arista EOS
"""

import argparse
import json
import logging
import re
import sys
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_COUNTER_PATTERNS = [
    ("input_errors", r"(\d+) input errors"),
    ("crc", r"(\d+) CRC"),
    ("frame", r"(\d+) frame"),
    ("output_errors", r"(\d+) output errors"),
    ("drops_input", r"(\d+) input drops"),
    ("drops_output", r"(\d+) output drops"),
    ("resets", r"(\d+) resets"),
    ("runts", r"(\d+) runts"),
    ("giants", r"(\d+) giants"),
    ("collisions", r"(\d+) collisions"),
]


def _parse_counters(block: str) -> Dict[str, int]:
    counters: Dict[str, int] = {}
    for key, pattern in _COUNTER_PATTERNS:
        m = re.search(pattern, block, re.IGNORECASE)
        counters[key] = int(m.group(1)) if m else 0
    return counters


def collect_errors(task: Task, interface_filter: Optional[str]) -> Result:
    """Run 'show interfaces' and parse error counters per interface."""
    platform = (task.host.platform or "ios").lower()

    if "nxos" in platform:
        cmd = "show interface"
    elif "eos" in platform or "arista" in platform:
        cmd = "show interfaces"
    else:
        cmd = "show interfaces"

    result = task.run(task=netmiko_send_command, command_string=cmd)
    raw = result.result

    # Split on interface headers — lines starting with a word char + slash or space
    blocks = re.split(r"(?=^\S)", raw, flags=re.MULTILINE)

    interfaces: List[Dict] = []
    for block in blocks:
        header = block.split("\n")[0].strip()
        if not header or header.startswith(" "):
            continue

        intf_name = header.split()[0]

        if interface_filter and not re.search(interface_filter, intf_name, re.IGNORECASE):
            continue

        counters = _parse_counters(block)
        total_errors = (
            counters["input_errors"]
            + counters["output_errors"]
            + counters["crc"]
            + counters["drops_input"]
            + counters["drops_output"]
        )

        interfaces.append({
            "interface": intf_name,
            "total_errors": total_errors,
            **counters,
        })

    interfaces.sort(key=lambda x: x["total_errors"], reverse=True)
    return Result(host=task.host, result=interfaces)


def _print_table(report: Dict[str, List[Dict]], threshold: int, min_errors: int) -> None:
    header = (
        f"{'Device':<16} {'Interface':<22} {'InErr':>7} {'CRC':>7} "
        f"{'OutErr':>7} {'Drops':>7} {'Resets':>6} {'Total':>7}"
    )
    print(header)
    print("-" * len(header))

    for device, interfaces in sorted(report.items()):
        if isinstance(interfaces, str):
            print(f"{device:<16}  ERROR: {interfaces}")
            continue
        for intf in interfaces:
            if intf["total_errors"] < min_errors:
                continue
            flag = " !" if intf["total_errors"] >= threshold else "  "
            drops = intf["drops_input"] + intf["drops_output"]
            print(
                f"{device:<16} {intf['interface']:<22} "
                f"{intf['input_errors']:>7} {intf['crc']:>7} "
                f"{intf['output_errors']:>7} {drops:>7} "
                f"{intf['resets']:>6} {intf['total_errors']:>7}{flag}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report interface error counters and flag problematic interfaces"
    )
    parser.add_argument("--config", default="config.yaml", help="Nornir config file")
    parser.add_argument(
        "--devices", help="Comma-separated device names (default: all)"
    )
    parser.add_argument(
        "--interface-filter",
        metavar="REGEX",
        help="Regex to restrict which interfaces are reported (e.g. 'GigabitEthernet')",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=1000,
        help="Total error count that marks an interface as flagged (default: 1000)",
    )
    parser.add_argument(
        "--min-errors",
        type=int,
        default=0,
        help="Hide interfaces with fewer total errors than this (default: 0)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config)
    except Exception as exc:
        logger.error("Failed to initialize Nornir: %s", exc)
        return 1

    if args.devices:
        names = [d.strip() for d in args.devices.split(",")]
        nr = nr.filter(F(name__in=names))

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check --devices or inventory.")
        return 1

    logger.debug("Targeting %d host(s)", len(nr.inventory.hosts))

    results = nr.run(
        task=collect_errors,
        interface_filter=args.interface_filter,
    )

    report: Dict[str, List[Dict]] = {}
    for hostname, multi in results.items():
        top = multi[0]
        if top.failed:
            report[hostname] = str(top.exception or top.result)
        else:
            report[hostname] = top.result

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_table(report, args.threshold, args.min_errors)
        if any(
            isinstance(v, list) and any(i["total_errors"] >= args.threshold for i in v)
            for v in report.values()
        ):
            print("\n  ! = exceeds threshold of", args.threshold, "total errors")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```