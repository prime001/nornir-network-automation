The prompt says "Output ONLY the script content" — they want the raw text, not a file write. Here it is:

"""
Device Health Monitor - Nornir Custom Processor Plugin

Collects CPU load, memory utilization, and uptime from network devices using
a custom Nornir Processor that emits threshold alerts in real time as tasks
complete, rather than post-hoc filtering of aggregated results.

Usage:
    python device_health_monitor.py --inventory hosts.yaml
    python device_health_monitor.py --inventory hosts.yaml --groups core,dist
    python device_health_monitor.py --inventory hosts.yaml --cpu-threshold 80 --output json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Nornir SimpleInventory: hosts.yaml (groups.yaml and defaults.yaml optional)
    Devices must support IOS-style 'show processes cpu/memory' output.
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.inventory import Host
from nornir.core.task import AggregatedResult, MultiResult, Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)


@dataclass
class HealthAlert:
    host: str
    metric: str
    value: float
    threshold: float


class HealthAlertProcessor:
    """Nornir Processor that fires threshold alerts as each host task completes."""

    def __init__(self, cpu_threshold: float, mem_threshold: float) -> None:
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.alerts: List[HealthAlert] = []

    def task_started(self, task: Task) -> None:
        logger.debug("task started: %s", task.name)

    def task_completed(self, task: Task, result: AggregatedResult) -> None:
        logger.debug("task completed: %s", task.name)

    def task_instance_started(self, task: Task, host: Host) -> None:
        pass

    def task_instance_completed(self, task: Task, host: Host, result: MultiResult) -> None:
        if result.failed:
            return
        metrics = host.data.get("health_metrics", {})
        cpu = metrics.get("cpu_load")
        mem = metrics.get("mem_used_pct")
        if cpu is not None and cpu >= self.cpu_threshold:
            self.alerts.append(HealthAlert(host.name, "cpu_load", cpu, self.cpu_threshold))
            logger.warning("[ALERT] %s CPU %.1f%% >= %.1f%%", host.name, cpu, self.cpu_threshold)
        if mem is not None and mem >= self.mem_threshold:
            self.alerts.append(HealthAlert(host.name, "mem_used_pct", mem, self.mem_threshold))
            logger.warning("[ALERT] %s MEM %.1f%% >= %.1f%%", host.name, mem, self.mem_threshold)

    def subtask_instance_started(self, task: Task, host: Host) -> None:
        pass

    def subtask_instance_completed(self, task: Task, host: Host, result: MultiResult) -> None:
        pass


def _parse_cpu_ios(output: str) -> Optional[float]:
    """Extract 5-second CPU% from 'show processes cpu' header line."""
    for line in output.splitlines():
        if "CPU utilization" in line:
            for segment in line.split(","):
                if "five" in segment.lower():
                    try:
                        raw = segment.split(":")[1].strip().rstrip("%").split("/")[0]
                        return float(raw)
                    except (IndexError, ValueError):
                        pass
    return None


def _parse_mem_ios(output: str) -> Optional[float]:
    """Compute used-memory % from 'show processes memory' Processor pool line."""
    for line in output.splitlines():
        if line.strip().startswith("Processor"):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    total, used = int(parts[1]), int(parts[2])
                    return round(used / total * 100, 1) if total else None
                except (ValueError, ZeroDivisionError):
                    pass
    return None


def collect_health(task: Task) -> Result:
    """Nornir task: gather CPU, memory, and uptime from a single device."""
    cpu_r = task.run(
        task=netmiko_send_command,
        command_string="show processes cpu | head 5",
        name="cpu",
    )
    mem_r = task.run(
        task=netmiko_send_command,
        command_string="show processes memory sorted | head 5",
        name="memory",
    )
    uptime_r = task.run(
        task=netmiko_send_command,
        command_string="show version | include uptime",
        name="uptime",
    )

    uptime_lines = (uptime_r.result or "").strip().splitlines()
    metrics = {
        "cpu_load": _parse_cpu_ios(cpu_r.result or ""),
        "mem_used_pct": _parse_mem_ios(mem_r.result or ""),
        "uptime": uptime_lines[0] if uptime_lines else "unknown",
    }
    task.host.data["health_metrics"] = metrics
    return Result(host=task.host, result=metrics)


def _build_nornir(inventory: str, groups: Optional[List[str]], workers: int) -> Nornir:
    nr = InitNornir(
        inventory={"plugin": "SimpleInventory", "options": {"host_file": inventory}},
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        logging={"enabled": False},
    )
    if groups:
        nr = nr.filter(filter_func=lambda h: any(g in h.groups for g in groups))
    return nr


def _print_table(health: Dict[str, dict], alerts: List[HealthAlert], failed: List[str]) -> None:
    print(f"\n{'Host':<24} {'CPU%':>6} {'Mem%':>6}  Uptime")
    print("-" * 75)
    for host in sorted(health):
        m = health[host]
        cpu = f"{m['cpu_load']:.1f}" if m["cpu_load"] is not None else "N/A"
        mem = f"{m['mem_used_pct']:.1f}" if m["mem_used_pct"] is not None else "N/A"
        uptime = (m.get("uptime") or "unknown")[:38]
        print(f"{host:<24} {cpu:>6} {mem:>6}  {uptime}")
    if failed:
        print(f"\nFailed ({len(failed)}): {', '.join(sorted(failed))}")
    if alerts:
        print(f"\nAlerts ({len(alerts)}):")
        for a in alerts:
            print(f"  [WARN] {a.host}: {a.metric}={a.value} >= threshold {a.threshold}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect CPU/memory/uptime from network devices")
    parser.add_argument("--inventory", required=True, help="Path to Nornir hosts.yaml")
    parser.add_argument("--groups", help="Comma-separated host groups to target")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent threads (default: 10)")
    parser.add_argument("--cpu-threshold", type=float, default=75.0, metavar="PCT",
                        help="CPU alert threshold %% (default: 75)")
    parser.add_argument("--mem-threshold", type=float, default=80.0, metavar="PCT",
                        help="Memory alert threshold %% (default: 80)")
    parser.add_argument("--output", choices=["table", "json"], default="table",
                        help="Output format (default: table)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    groups = [g.strip() for g in args.groups.split(",")] if args.groups else None
    processor = HealthAlertProcessor(args.cpu_threshold, args.mem_threshold)

    try:
        nr = _build_nornir(args.inventory, groups, args.workers)
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        sys.exit(1)

    agg = nr.with_processors([processor]).run(task=collect_health, name="device_health")

    health: Dict[str, dict] = {}
    failed: List[str] = []
    for host, multi in agg.items():
        if multi.failed:
            failed.append(host)
            logger.error("host %s failed: %s", host, multi[0].exception)
        else:
            health[host] = multi[0].result

    if args.output == "json":
        print(json.dumps({
            "health": health,
            "alerts": [{"host": a.host, "metric": a.metric, "value": a.value,
                        "threshold": a.threshold} for a in processor.alerts],
            "failed": failed,
        }, indent=2))
    else:
        _print_table(health, processor.alerts, failed)

    sys.exit(1 if failed or processor.alerts else 0)


if __name__ == "__main__":
    main()