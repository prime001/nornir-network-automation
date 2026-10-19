```
"""
Device Health Monitor — CPU, memory, and uptime across the fleet.

Collects CPU utilization, memory usage, and system uptime from network devices
using Nornir with netmiko. Flags devices that exceed configurable thresholds
and outputs a sortable summary table.

Usage:
    python 072_device_health_monitor.py [options]

    # Check all devices, alert when CPU > 70% or memory > 80%
    python 072_device_health_monitor.py --cpu-threshold 70 --mem-threshold 80

    # Filter to a specific group, output JSON
    python 072_device_health_monitor.py --filter-group core --output json

    # Override credentials at runtime
    python 072_device_health_monitor.py --username admin --password secret

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
"""

import argparse
import json
import logging
import re
import sys
from typing import Optional

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_cpu(output: str) -> Optional[float]:
    """Extract five-second CPU utilization percentage."""
    match = re.search(r"five seconds:\s+(\d+)%", output, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r"CPU utilization for five seconds:\s+(\d+)%", output, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def parse_memory(output: str) -> Optional[float]:
    """Return used-memory percentage from processor pool output."""
    match = re.search(r"Processor\s+Pool Total:\s+(\d+)\s+Used:\s+(\d+)", output, re.IGNORECASE)
    if match:
        total, used = int(match.group(1)), int(match.group(2))
        return round(used / total * 100, 1) if total else None
    match = re.search(r"Total:\s+(\d+),\s+Used:\s+(\d+)", output, re.IGNORECASE)
    if match:
        total, used = int(match.group(1)), int(match.group(2))
        return round(used / total * 100, 1) if total else None
    return None


def parse_uptime(output: str) -> str:
    match = re.search(r"uptime is (.+)", output, re.IGNORECASE)
    return match.group(1).strip() if match else "unknown"


def collect_health(task: Task) -> Result:
    """Gather CPU, memory, and uptime for a single device."""
    cpu_out = task.run(
        task=netmiko_send_command,
        command_string="show processes cpu | include CPU utilization",
        name="cpu",
    )
    mem_out = task.run(
        task=netmiko_send_command,
        command_string="show processes memory | include Processor",
        name="memory",
    )
    ver_out = task.run(
        task=netmiko_send_command,
        command_string="show version | include uptime",
        name="uptime",
    )

    cpu = parse_cpu(cpu_out.result)
    mem = parse_memory(mem_out.result)
    uptime = parse_uptime(ver_out.result)

    return Result(
        host=task.host,
        result={"cpu_pct": cpu, "mem_pct": mem, "uptime": uptime},
    )


def build_report(nr_results, cpu_threshold: float, mem_threshold: float) -> list[dict]:
    rows = []
    for hostname, multi in nr_results.items():
        if multi.failed:
            rows.append({
                "host": hostname,
                "cpu_pct": None,
                "mem_pct": None,
                "uptime": "N/A",
                "alert": "CONNECTION_FAILED",
            })
            continue

        data = multi[0].result
        cpu = data.get("cpu_pct")
        mem = data.get("mem_pct")
        alerts = []
        if cpu is not None and cpu > cpu_threshold:
            alerts.append(f"CPU>{cpu_threshold}%")
        if mem is not None and mem > mem_threshold:
            alerts.append(f"MEM>{mem_threshold}%")

        rows.append({
            "host": hostname,
            "cpu_pct": cpu,
            "mem_pct": mem,
            "uptime": data.get("uptime", "unknown"),
            "alert": ", ".join(alerts) if alerts else "OK",
        })
    return sorted(rows, key=lambda r: (r["alert"] == "OK", r["host"]))


def print_table(rows: list[dict]) -> None:
    header = f"{'HOST':<20} {'CPU%':>6} {'MEM%':>6}  {'UPTIME':<35} {'STATUS'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        cpu = f"{r['cpu_pct']:>5.1f}" if r["cpu_pct"] is not None else "  N/A"
        mem = f"{r['mem_pct']:>5.1f}" if r["mem_pct"] is not None else "  N/A"
        flag = "!" if r["alert"] not in ("OK", "CONNECTION_FAILED") else " "
        print(f"{r['host']:<20} {cpu}  {mem}  {r['uptime']:<35} {flag}{r['alert']}")


def main(args: argparse.Namespace) -> int:
    nr: Nornir = InitNornir(config_file=args.config)

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password
    if args.filter_group:
        nr = nr.filter(groups=lambda groups: args.filter_group in groups)

    if not nr.inventory.hosts:
        logger.error("No hosts matched the inventory filter.")
        return 1

    results = nr.run(task=collect_health, name="device_health")

    rows = build_report(results, args.cpu_threshold, args.mem_threshold)

    if args.output == "json":
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows)

    alerts = sum(1 for r in rows if r["alert"] not in ("OK",))
    if alerts:
        logger.warning("%d device(s) exceeded health thresholds.", alerts)
        return 2
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Collect CPU, memory, and uptime health data from network devices."
    )
    parser.add_argument("--config", default="config.yaml", help="Nornir config file")
    parser.add_argument("--username", help="Override inventory username")
    parser.add_argument("--password", help="Override inventory password")
    parser.add_argument(
        "--filter-group", metavar="GROUP", help="Restrict to a single inventory group"
    )
    parser.add_argument(
        "--cpu-threshold", type=float, default=80.0, metavar="PCT",
        help="CPU %% alert threshold (default: 80)",
    )
    parser.add_argument(
        "--mem-threshold", type=float, default=85.0, metavar="PCT",
        help="Memory %% alert threshold (default: 85)",
    )
    parser.add_argument(
        "--output", choices=["table", "json"], default="table",
        help="Output format (default: table)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    sys.exit(main(args))
```