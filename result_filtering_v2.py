```python
#!/usr/bin/env python3
"""
Device Health Dashboard - Network Device Performance Monitor

Purpose:
    Collects and monitors key health metrics from network devices including
    CPU utilization, memory usage, uptime, and temperature. Compares metrics
    against configurable thresholds and generates actionable health reports.

Usage:
    python device_health_dashboard.py --inventory config.yaml \
        --username admin --password secret \
        --threshold-cpu 80 --threshold-memory 85

Prerequisites:
    - nornir >= 3.0
    - napalm
    - paramiko or netmiko backend
"""

import argparse
import logging
from typing import Dict, Optional
from dataclasses import dataclass
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


@dataclass
class HealthMetrics:
    """Device health measurement snapshot."""
    hostname: str
    uptime_seconds: int
    cpu_usage: Optional[float]
    memory_usage: Optional[float]
    health_status: str
    alerts: list


def get_device_health(task: Task, thresholds: Dict) -> Result:
    """Collect and analyze device health metrics via NAPALM."""
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        facts = facts_result[0].result

        uptime = facts.get("uptime", 0)
        cpu = facts.get("cpu_utilization", 0)
        memory = facts.get("memory_usage", {})
        memory_pct = memory.get("used_percent", 0) if isinstance(memory, dict) else 0

        alerts = []
        status = "healthy"

        if cpu > thresholds["cpu"]:
            status = "warning"
            alerts.append(f"CPU {cpu:.1f}% exceeds threshold {thresholds['cpu']}%")

        if memory_pct > thresholds["memory"]:
            status = "warning"
            alerts.append(f"Memory {memory_pct:.1f}% exceeds threshold {thresholds['memory']}%")

        if uptime < thresholds["uptime"]:
            status = "alert"
            alerts.append(f"Device recently rebooted: {uptime}s uptime")

        metrics = HealthMetrics(
            hostname=task.host.name,
            uptime_seconds=uptime,
            cpu_usage=cpu,
            memory_usage=memory_pct,
            health_status=status,
            alerts=alerts,
        )

        return Result(host=task.host, result=metrics)

    except Exception as e:
        logger.error(f"Health check failed for {task.host.name}: {e}")
        return Result(host=task.host, failed=True, exception=e)


def format_uptime(seconds: int) -> str:
    """Convert seconds to human-readable uptime string."""
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    return f"{days}d {hours}h {minutes}m"


def print_health_report(results: Dict) -> None:
    """Print formatted health report from task results."""
    metrics = [r.result for r in results.values() if r.result and not r.failed]

    if not metrics:
        print("No metrics collected successfully")
        return

    healthy = sum(1 for m in metrics if m.health_status == "healthy")
    warning = sum(1 for m in metrics if m.health_status == "warning")
    alert = sum(1 for m in metrics if m.health_status == "alert")

    print(f"\n{'='*110}")
    print(f"{'DEVICE HEALTH DASHBOARD':^110}")
    print(f"{'='*110}")
    print(f"\nOverall Status: {healthy} healthy | {warning} warning | {alert} alert\n")
    print(f"{'Hostname':<25} {'Status':<12} {'CPU %':<12} {'Memory %':<12} {'Uptime':<25}")
    print(f"{'-'*110}")

    for metric in sorted(metrics, key=lambda x: x.hostname):
        symbol = "✓" if metric.health_status == "healthy" else ("⚠" if metric.health_status == "warning" else "✗")
        cpu_str = f"{metric.cpu_usage:.1f}" if metric.cpu_usage is not None else "N/A"
        mem_str = f"{metric.memory_usage:.1f}" if metric.memory_usage is not None else "N/A"

        print(f"{metric.hostname:<25} {symbol} {metric.health_status:<10} {cpu_str:<11} {mem_str:<11} {format_uptime(metric.uptime_seconds):<25}")

        for alert in metric.alerts:
            print(f"  ├─ {alert}")

    print(f"{'='*110}\n")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Monitor network device health metrics")
    parser.add_argument("--inventory", default="config.yaml", help="Nornir inventory config file")
    parser.add_argument("--hosts", help="Comma-separated list of hostnames to monitor")
    parser.add_argument("--username", required=True, help="Device username")
    parser.add_argument("--password", required=True, help="Device password")
    parser.add_argument("--threshold-cpu", type=float, default=80, help="CPU usage alert threshold %%")
    parser.add_argument("--threshold-memory", type=float, default=85, help="Memory usage alert threshold %%")
    parser.add_argument("--threshold-uptime", type=int, default=3600, help="Minimum acceptable uptime in seconds")
    parser.add_argument("--loglevel", default="INFO", help="Logging level")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.loglevel))

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.hosts:
            hostnames = args.hosts.split(",")
            nr = nr.filter(name__in=hostnames)

        if not nr.inventory.hosts:
            logger.warning("No devices found matching filter criteria")
            return

        thresholds = {
            "cpu": args.threshold_cpu,
            "memory": args.threshold_memory,
            "uptime": args.threshold_uptime,
        }

        logger.info(f"Checking health metrics for {len(nr.inventory.hosts)} devices")
        results = nr.run(task=get_device_health, thresholds=thresholds)

        print_health_report(results)

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```