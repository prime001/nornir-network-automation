```python
"""
Device Health Monitor using Nornir

Monitors network device health by gathering key performance metrics
(uptime, vendor, model) and comparing them against configured thresholds.
Generates alerts for devices failing health checks.

Prerequisites:
  - Nornir installed with NAPALM plugin
  - Network inventory configured (hosts.yml)
  - SNMP or SSH access to network devices
  - Network device facts accessible via NAPALM getters

Usage:
  python device_health_monitor.py --inventory hosts.yml --users admin --password pass
  python device_health_monitor.py --inventory hosts.yml --thresholds uptime:24
  python device_health_monitor.py --devices .* --output health_report.json

Options:
  --inventory: Path to nornir inventory file (default: hosts.yml)
  --users: Device username (required)
  --password: Device password (required)
  --thresholds: Health thresholds as 'metric:value' pairs
  --devices: Filter devices by regex pattern
  --output: Save JSON report to file
  --loglevel: Logging level (DEBUG|INFO|WARNING|ERROR)
"""

import argparse
import json
import logging
from typing import Dict, Any, List

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get_facts


logger = logging.getLogger(__name__)


def gather_device_facts(task: Task) -> Result:
    """Gather device facts using NAPALM."""
    try:
        facts_result = task.run(napalm_get_facts, getters=["facts"])
        device_facts = facts_result[0].result

        health_data = {
            "device": task.host.name,
            "vendor": device_facts.get("vendor", "unknown"),
            "model": device_facts.get("model", "unknown"),
            "serial": device_facts.get("serial_number", "unknown"),
            "uptime_seconds": device_facts.get("uptime", 0),
            "uptime_hours": device_facts.get("uptime", 0) / 3600,
            "os_version": device_facts.get("os_version", "unknown"),
        }

        return Result(host=task.host, result=health_data, failed=False)

    except Exception as e:
        logger.error(f"Failed to gather facts from {task.host.name}: {e}")
        return Result(
            host=task.host,
            result=None,
            failed=True,
            exception=e
        )


def evaluate_health_status(
    health_data: Dict[str, Any],
    thresholds: Dict[str, float]
) -> Dict[str, Any]:
    """Evaluate device health against thresholds."""
    alerts: List[str] = []

    uptime_hours = health_data.get("uptime_hours", 0)
    min_uptime = thresholds.get("uptime", 24)

    if uptime_hours < min_uptime:
        alerts.append(
            f"Low uptime: {uptime_hours:.1f}h (threshold: {min_uptime}h)"
        )

    return {
        "device": health_data["device"],
        "vendor": health_data["vendor"],
        "model": health_data["model"],
        "uptime_hours": round(uptime_hours, 2),
        "os_version": health_data["os_version"],
        "alerts": alerts,
        "healthy": len(alerts) == 0,
    }


def parse_thresholds(threshold_str: str) -> Dict[str, float]:
    """Parse threshold string into dictionary."""
    thresholds = {"uptime": 24}

    if not threshold_str:
        return thresholds

    for item in threshold_str.split():
        if ":" in item:
            try:
                key, value = item.split(":")
                thresholds[key] = float(value)
            except ValueError:
                logger.warning(f"Invalid threshold format: {item}")

    return thresholds


def print_report(health_report: List[Dict[str, Any]], failed_count: int) -> None:
    """Print formatted health report to console."""
    healthy_count = sum(1 for h in health_report if h["healthy"])
    total_count = len(health_report)

    print("\n" + "=" * 80)
    print(f"DEVICE HEALTH REPORT: {healthy_count}/{total_count} devices healthy")
    print("=" * 80)

    for status in health_report:
        status_indicator = "✓" if status["healthy"] else "✗"
        print(
            f"\n{status_indicator} {status['device']:20s} | "
            f"{status['model']:25s} | "
            f"Uptime: {status['uptime_hours']:.1f}h"
        )

        if status["alerts"]:
            for alert in status["alerts"]:
                print(f"    ⚠ {alert}")

    if failed_count > 0:
        print(f"\n⚠ {failed_count} device(s) failed to report")

    print("=" * 80)


def main() -> int:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor network device health and uptime"
    )
    parser.add_argument(
        "--inventory",
        default="hosts.yml",
        help="Path to nornir inventory file"
    )
    parser.add_argument(
        "--users",
        required=True,
        help="Device username for authentication"
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Device password for authentication"
    )
    parser.add_argument(
        "--thresholds",
        help="Health thresholds (e.g., 'uptime:24')"
    )
    parser.add_argument(
        "--devices",
        help="Filter devices by regex pattern"
    )
    parser.add_argument(
        "--output",
        help="Save JSON report to file"
    )
    parser.add_argument(
        "--loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    thresholds = parse_thresholds(args.thresholds)

    try:
        nr = InitNornir(config_file=args.inventory)
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return 1

    if args.devices:
        nr = nr.filter(name__regex=args.devices)

    nr.inventory.defaults.username = args.users
    nr.inventory.defaults.password = args.password

    logger.info(
        f"Gathering health metrics from {len(nr.inventory.hosts)} devices"
    )

    results = nr.run(task=gather_device_facts)

    health_report = []
    failed_count = 0

    for host_name, multi_result in results.items():
        if multi_result.failed or not multi_result[0].result:
            failed_count += 1
            logger.warning(f"Failed to collect metrics from {host_name}")
            continue

        health_data = multi_result[0].result
        status = evaluate_health_status(health_data, thresholds)
        health_report.append(status)

    print_report(health_report, failed_count)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(health_report, f, indent=2)
        logger.info(f"Report saved to {args.output}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    exit(main())
```