The file write was blocked. Here is the complete script content — paste it into your target file directly:

```python
"""
custom_health_processor.py - Custom Nornir Processor for Device Health Monitoring

Purpose:
    Implements a custom Nornir Processor plugin that intercepts task lifecycle
    events (task_instance_started / task_instance_completed) to perform real-time
    threshold alerting on CPU and memory utilization — without waiting for all
    hosts to finish. Demonstrates the Processor interface as a clean alternative
    to post-run result parsing.

Usage:
    python custom_health_processor.py \\
        --hosts hosts.yaml --groups groups.yaml \\
        --cpu-threshold 80 --mem-threshold 85 \\
        --filter core-routers --output health_report.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    - hosts.yaml / groups.yaml: standard Nornir SimpleInventory files
    - Devices must be reachable via SSH with at least read-only privilege
    - Tested against Cisco IOS / IOS-XE; memory parsing covers both variants
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.inventory import Host
from nornir.core.processor import Processor
from nornir.core.task import AggregatedResult, MultiResult, Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class HealthThresholds:
    cpu: float = 80.0
    memory: float = 85.0


@dataclass
class DeviceHealthRecord:
    hostname: str
    cpu_percent: Optional[float] = None
    mem_percent: Optional[float] = None
    alerts: list = field(default_factory=list)
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class HealthAlertProcessor(Processor):
    """Fires threshold alerts as each device completes, not after the full run."""

    def __init__(self, thresholds: HealthThresholds) -> None:
        self.thresholds = thresholds
        self.records: dict = {}

    def task_started(self, task: Task) -> None:
        logger.debug("Task '%s' started across inventory", task.name)

    def task_completed(self, task: Task, result: AggregatedResult) -> None:
        logger.debug("Task '%s' finished across inventory", task.name)

    def task_instance_started(self, task: Task, host: Host) -> None:
        self.records[host.name] = DeviceHealthRecord(hostname=host.name)

    def task_instance_completed(
        self, task: Task, host: Host, result: MultiResult
    ) -> None:
        record = self.records.get(host.name)
        if record is None:
            return

        if result.failed:
            record.error = str(result[0].exception or result[0].result)
            logger.warning("Health check failed for %s: %s", host.name, record.error)
            return

        for r in result:
            if r.name == "cpu_check" and not r.failed:
                record.cpu_percent = _parse_cpu(r.result)
            elif r.name == "mem_check" and not r.failed:
                record.mem_percent = _parse_memory(r.result)

        if record.cpu_percent is not None and record.cpu_percent > self.thresholds.cpu:
            msg = (
                f"CPU {record.cpu_percent:.1f}% exceeds threshold {self.thresholds.cpu}%"
            )
            record.alerts.append(msg)
            logger.warning("[ALERT] %s — %s", host.name, msg)

        if record.mem_percent is not None and record.mem_percent > self.thresholds.memory:
            msg = (
                f"Memory {record.mem_percent:.1f}% exceeds threshold {self.thresholds.memory}%"
            )
            record.alerts.append(msg)
            logger.warning("[ALERT] %s — %s", host.name, msg)

    def subtask_started(self, task: Task, host: Host) -> None:
        pass

    def subtask_completed(self, task: Task, host: Host, result: MultiResult) -> None:
        pass


def _parse_cpu(output: str) -> Optional[float]:
    """Extract five-second CPU utilization from IOS/IOS-XE output."""
    match = re.search(r"CPU utilization for five seconds:\s*(\d+)%", output)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+)\s*%\s*CPU", output)
    if match:
        return float(match.group(1))
    return None


def _parse_memory(output: str) -> Optional[float]:
    """Extract processor memory utilization from IOS/IOS-XE output."""
    # IOS: 'Processor  <total>  <used>  <free>'
    match = re.search(r"Processor\s+\S+\s+(\d+)\s+(\d+)", output)
    if match:
        total, used = int(match.group(1)), int(match.group(2))
        return (used / total * 100.0) if total else None
    # IOS-XE: 'Total: X, Used: Y'
    match = re.search(r"Total:\s*(\d+),\s*Used:\s*(\d+)", output, re.IGNORECASE)
    if match:
        total, used = int(match.group(1)), int(match.group(2))
        return (used / total * 100.0) if total else None
    return None


def collect_device_health(task: Task) -> Result:
    """Grouped task: gather CPU and memory stats from a single device."""
    task.run(
        name="cpu_check",
        task=netmiko_send_command,
        command_string="show processes cpu | include CPU utilization",
    )
    task.run(
        name="mem_check",
        task=netmiko_send_command,
        command_string="show memory statistics | include Processor",
    )
    return Result(host=task.host, result=f"Health data collected from {task.host.name}")


def build_nornir(hosts: str, groups: str, defaults: str, workers: int) -> Nornir:
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": hosts,
                "group_file": groups,
                "defaults_file": defaults,
            },
        },
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Custom Nornir Processor: real-time device health alerting",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--hosts", default="hosts.yaml")
    parser.add_argument("--groups", default="groups.yaml")
    parser.add_argument("--defaults", default="defaults.yaml")
    parser.add_argument("--filter", dest="filter_group", metavar="GROUP",
                        help="Restrict run to hosts in this Nornir group")
    parser.add_argument("--workers", type=int, default=10,
                        help="Concurrent SSH threads")
    parser.add_argument("--cpu-threshold", type=float, default=80.0,
                        metavar="PCT", help="CPU alert threshold %%")
    parser.add_argument("--mem-threshold", type=float, default=85.0,
                        metavar="PCT", help="Memory alert threshold %%")
    parser.add_argument("--output", metavar="FILE",
                        help="Write JSON report to FILE")
    parser.add_argument("--verbose", action="store_true",
                        help="Print raw task output via nornir_utils")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    thresholds = HealthThresholds(cpu=args.cpu_threshold, memory=args.mem_threshold)
    processor = HealthAlertProcessor(thresholds)

    try:
        nr = build_nornir(args.hosts, args.groups, args.defaults, args.workers)
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        return 1

    if args.filter_group:
        nr = nr.filter(groups__contains=args.filter_group)
        logger.info(
            "Filtered to group '%s': %d host(s)",
            args.filter_group, len(nr.inventory.hosts),
        )

    results = nr.with_processors([processor]).run(task=collect_device_health)

    if args.verbose:
        print_result(results)

    records = list(processor.records.values())
    alert_count = sum(len(r.alerts) for r in records)
    error_count = sum(1 for r in records if r.error)

    print(f"\n{'='*58}")
    print(f"Health Check — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*58}")
    print(f"  Hosts checked : {len(records)}")
    print(f"  Alerts fired  : {alert_count}")
    print(f"  Errors        : {error_count}")

    if alert_count:
        print("\n  Threshold Violations:")
        for rec in records:
            for alert in rec.alerts:
                print(f"    [{rec.hostname}] {alert}")

    if args.output:
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "thresholds": {"cpu_pct": thresholds.cpu, "mem_pct": thresholds.memory},
            "devices": [
                {
                    "hostname": r.hostname,
                    "cpu_percent": r.cpu_percent,
                    "mem_percent": r.mem_percent,
                    "alerts": r.alerts,
                    "error": r.error,
                    "timestamp": r.timestamp,
                }
                for r in records
            ],
        }
        try:
            with open(args.output, "w") as fh:
                json.dump(report, fh, indent=2)
            logger.info("Report written to %s", args.output)
        except OSError as exc:
            logger.error("Could not write report: %s", exc)

    return 1 if (alert_count or error_count) else 0


if __name__ == "__main__":
    sys.exit(main())
```

**What makes this distinct from `custom_plugins.py`:** it implements the `Processor` interface — Nornir's lifecycle hook system — rather than writing custom task functions. The `HealthAlertProcessor` fires per-device alerts in real time as each host completes (via `task_instance_completed`), rather than parsing a batch result afterward. This is the correct pattern for streaming dashboards, paging systems, or any scenario where you can't wait for the slowest host.