```python
#!/usr/bin/env python3
"""
Device Health Monitor - Collects and reports device health metrics.

Purpose:
  Monitor device CPU, memory, and disk usage across the network inventory.
  Generates alerts when metrics exceed configurable thresholds.

Usage:
  python device_health_monitor.py --threshold-cpu 80 --threshold-memory 85
  python device_health_monitor.py --device switch01 --verbose

Prerequisites:
  - Nornir configured with device inventory
  - Devices reachable via SSH/API
  - Network connectivity and proper credentials
"""

import logging
import argparse
import sys
from datetime import datetime
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_napalm.tasks import napalm_get

logger = logging.getLogger(__name__)


def collect_device_health(
    task: Task,
    threshold_cpu: int,
    threshold_mem: int,
    threshold_disk: int,
) -> Result:
    """Collect health metrics from device using NAPALM."""
    try:
        facts_result = task.run(
            name="gather_facts",
            task=napalm_get,
            getters=["facts"],
        )

        if facts_result[0].failed:
            return Result(host=task.host, failed=True, result="Failed to retrieve facts")

        facts = facts_result[0].result.get("facts", {})

        health_metrics = {
            "hostname": task.host.name,
            "device_type": task.host.platform or "unknown",
            "model": facts.get("model", "N/A"),
            "os_version": facts.get("os_version", "N/A"),
            "uptime_seconds": facts.get("uptime_seconds", 0),
            "serial_number": facts.get("serial_number", "N/A"),
            "timestamp": datetime.now().isoformat(),
        }

        cpu_usage = task.host.get("cpu_usage", 0)
        memory_usage = task.host.get("memory_usage", 0)
        disk_usage = task.host.get("disk_usage", 0)

        health_metrics["cpu_usage"] = cpu_usage
        health_metrics["memory_usage"] = memory_usage
        health_metrics["disk_usage"] = disk_usage

        alerts = []
        if cpu_usage > threshold_cpu:
            alerts.append(f"CPU {cpu_usage}% exceeds {threshold_cpu}%")
        if memory_usage > threshold_mem:
            alerts.append(f"Memory {memory_usage}% exceeds {threshold_mem}%")
        if disk_usage > threshold_disk:
            alerts.append(f"Disk {disk_usage}% exceeds {threshold_disk}%")

        health_metrics["alerts"] = alerts
        health_metrics["status"] = "OK" if not alerts else "ALERT"

        return Result(host=task.host, result=health_metrics)

    except Exception as e:
        logger.error(f"{task.host.name}: Error - {str(e)}")
        return Result(host=task.host, failed=True, result=str(e))


def format_uptime(seconds: int) -> str:
    """Convert uptime seconds to human-readable format."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def main():
    parser = argparse.ArgumentParser(
        description="Monitor device health across network inventory"
    )
    parser.add_argument(
        "--device",
        help="Target specific device by name",
    )
    parser.add_argument(
        "--threshold-cpu",
        type=int,
        default=80,
        help="CPU usage threshold in percent (default: 80)",
    )
    parser.add_argument(
        "--threshold-memory",
        type=int,
        default=85,
        help="Memory usage threshold in percent (default: 85)",
    )
    parser.add_argument(
        "--threshold-disk",
        type=int,
        default=90,
        help="Disk usage threshold in percent (default: 90)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging output",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        logger.info("Initializing Nornir inventory...")
        nr = InitNornir()

        if args.device:
            nr = nr.filter(F(name=args.device))
            if not nr.inventory.hosts:
                logger.error(f"Device '{args.device}' not found in inventory")
                sys.exit(1)

        logger.info(f"Starting health check on {len(nr.inventory.hosts)} device(s)...")

        results = nr.run(
            name="collect_health",
            task=collect_device_health,
            threshold_cpu=args.threshold_cpu,
            threshold_mem=args.threshold_memory,
            threshold_disk=args.threshold_disk,
        )

        print("\n" + "=" * 80)
        print("DEVICE HEALTH REPORT")
        print("=" * 80)

        summary = {"total": 0, "ok": 0, "alert": 0, "failed": 0}

        for host_name in sorted(results.keys()):
            host_results = results[host_name]
            summary["total"] += 1

            if host_results[0].failed:
                summary["failed"] += 1
                print(f"\n{host_name:30} [FAILED]")
                print(f"  Error: {host_results[0].result}")
                continue

            metrics = host_results[0].result
            status = metrics["status"]
            summary[status.lower()] += 1

            status_symbol = "✓" if status == "OK" else "⚠"
            print(f"\n{host_name:30} [{status}] {status_symbol}")
            print(f"  Model: {metrics['model']}")
            print(f"  OS: {metrics['os_version']}")
            print(f"  Uptime: {format_uptime(metrics['uptime_seconds'])}")
            print(
                f"  Resources: CPU {metrics['cpu_usage']}% | "
                f"Memory {metrics['memory_usage']}% | "
                f"Disk {metrics['disk_usage']}%"
            )

            if metrics["alerts"]:
                for alert in metrics["alerts"]:
                    print(f"  ⚠ {alert}")

        print("\n" + "=" * 80)
        print(
            f"SUMMARY: Total={summary['total']} OK={summary['ok']} "
            f"ALERT={summary['alert']} FAILED={summary['failed']}"
        )
        print("=" * 80 + "\n")

        exit_code = 0 if summary["failed"] == 0 and summary["alert"] == 0 else 1
        sys.exit(exit_code)

    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```