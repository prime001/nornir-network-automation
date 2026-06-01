```python
"""
Device Health and System Metrics Collector

Gathers comprehensive health metrics from network devices including uptime,
interface status, and system information. Useful for capacity planning,
monitoring, and health assessments across device fleet.

Usage:
    python device_health_check.py -H inventory.yaml -u admin -p password
    python device_health_check.py -H inventory.yaml -u admin -p password -d router1,router2
    python device_health_check.py -H inventory.yaml -u admin -p password --format json

Prerequisites:
    - Nornir installed with NAPALM plugin
    - Network inventory file (YAML format)
    - Device credentials (username/password or SSH key)
    - Device SSH/NETCONF access enabled

Output:
    - Health summary table (default text format)
    - JSON output (--format json)
    - Device-specific metrics including uptime, interface count, software version
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging with timestamp and level."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=level,
    )
    return logging.getLogger(__name__)


def collect_device_health(task: Task) -> Result:
    """Collect health metrics from a device using NAPALM getters."""
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        interfaces_result = task.run(napalm_get, getters=["interfaces"])

        facts_data = facts_result[0].result.get("facts", {})
        interfaces_data = interfaces_result[0].result.get("interfaces", {})

        up_count = sum(
            1 for iface in interfaces_data.values()
            if iface.get("state") == "up"
        )

        health_metrics = {
            "hostname": facts_data.get("hostname", "unknown"),
            "vendor": facts_data.get("vendor", "unknown"),
            "model": facts_data.get("model", "unknown"),
            "os_version": facts_data.get("os_version", "unknown"),
            "uptime_seconds": facts_data.get("uptime_seconds", 0),
            "serial_number": facts_data.get("serial_number", "N/A"),
            "total_interfaces": len(interfaces_data),
            "up_interfaces": up_count,
            "down_interfaces": len(interfaces_data) - up_count,
            "collected_at": datetime.now().isoformat(),
        }

        return Result(host=task.host, result=health_metrics)

    except Exception as e:
        return Result(
            host=task.host,
            result=None,
            failed=True,
            exception=e,
        )


def format_uptime(seconds: int) -> str:
    """Convert seconds to human-readable uptime format."""
    if not seconds:
        return "N/A"
    uptime = timedelta(seconds=seconds)
    days = uptime.days
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def print_text_report(health_data: Dict) -> None:
    """Print health metrics as formatted text table."""
    print("\n" + "=" * 110)
    print("DEVICE HEALTH REPORT")
    print("=" * 110)
    print(
        f"{'Hostname':<18} {'Vendor':<10} {'Model':<20} "
        f"{'Uptime':<15} {'Interfaces':<15} {'OS Version':<15}"
    )
    print("-" * 110)

    for hostname in sorted(health_data.keys()):
        metrics = health_data[hostname]
        uptime_str = format_uptime(metrics["uptime_seconds"])
        iface_str = (
            f"{metrics['up_interfaces']}/{metrics['total_interfaces']}"
        )
        print(
            f"{metrics['hostname']:<18} {metrics['vendor']:<10} "
            f"{metrics['model']:<20} {uptime_str:<15} {iface_str:<15} "
            f"{metrics['os_version']:<15}"
        )

    print("=" * 110 + "\n")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-H",
        "--hosts",
        required=True,
        help="Path to inventory file (YAML)",
    )
    parser.add_argument(
        "-u",
        "--username",
        required=True,
        help="Device username",
    )
    parser.add_argument(
        "-p",
        "--password",
        required=True,
        help="Device password",
    )
    parser.add_argument(
        "-d",
        "--devices",
        help="Comma-separated list of device names (optional filter)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()
    logger = setup_logging(args.log_level)

    try:
        logger.info("Initializing Nornir inventory")
        nr = InitNornir(
            inventory={
                "plugin": "SimpleInventory",
                "options": {"host_file": args.hosts},
            }
        )

        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name__in=device_list)
            logger.info(f"Filtered to {len(nr.inventory.hosts)} devices")
        else:
            logger.info(f"Loaded {len(nr.inventory.hosts)} devices")

        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password

        logger.info("Collecting health metrics from all devices")
        results = nr.run(task=collect_device_health)

        health_data = {}
        failed_hosts = []

        for host_name, multi_result in results.items():
            if multi_result[0].result:
                health_data[host_name] = multi_result[0].result
            else:
                failed_hosts.append(host_name)
                logger.warning(
                    f"Failed to collect from {host_name}: "
                    f"{multi_result[0].exception}"
                )

        if not health_data:
            logger.error("No health data collected from any device")
            return 1

        if args.format == "json":
            print(json.dumps(health_data, indent=2))
        else:
            print_text_report(health_data)

        if failed_hosts:
            logger.warning(f"Failed on {len(failed_hosts)} device(s)")
            return 1

        logger.info("Health check completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```