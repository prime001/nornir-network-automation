```python
"""
Device Health Check Automation

This script connects to network devices via SSH using Nornir and collects
health metrics including CPU usage, memory utilization, and device uptime.

Usage:
    python device_health_check.py -u admin -p password -i inventory.yaml
    python device_health_check.py -u admin -p password -d device1
    python device_health_check.py -u admin -p password -o csv

Prerequisites:
    - Nornir >= 3.0
    - netmiko or napalm for device connectivity
    - Network devices accessible via SSH
    - Valid credentials for target devices

Features:
    - Connects to multiple devices in parallel
    - Gathers CPU, memory, and uptime metrics
    - Supports custom inventory files
    - Outputs results as table or CSV
    - Includes comprehensive error handling and logging
"""

import logging
import argparse
import csv
import sys
from datetime import datetime
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_utils.plugins.tasks.commands import netmiko_send_command


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging for the script."""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("device_health_check.log"),
            logging.StreamHandler(),
        ],
    )


def get_health_metrics(task: Task) -> Result:
    """Gather health metrics from a device."""
    logger = logging.getLogger(__name__)
    metrics = {}

    try:
        device_type = task.host.platform
        
        if not device_type or "cisco" not in device_type.lower():
            logger.warning(f"Device type {device_type} may not be fully supported")

        logger.debug(f"Connecting to {task.host.name}")
        
        r1 = task.run(
            netmiko_send_command,
            command_string="show processes cpu | include CPU",
        )
        metrics["cpu"] = r1[0].result.strip() if r1[0].result else "N/A"

        r2 = task.run(
            netmiko_send_command,
            command_string="show processes memory | include Used",
        )
        metrics["memory"] = r2[0].result.strip() if r2[0].result else "N/A"

        r3 = task.run(
            netmiko_send_command,
            command_string="show version | include uptime",
        )
        metrics["uptime"] = r3[0].result.strip() if r3[0].result else "N/A"

        logger.info(f"Successfully gathered metrics from {task.host.name}")
        return Result(host=task.host, result=metrics)

    except Exception as e:
        logger.error(f"Error gathering metrics from {task.host.name}: {e}")
        return Result(host=task.host, failed=True, result=str(e))


def export_to_csv(results: Dict, output_file: str) -> None:
    """Export health metrics to CSV file."""
    logger = logging.getLogger(__name__)

    try:
        with open(output_file, "w", newline="") as csvfile:
            fieldnames = ["hostname", "timestamp", "cpu", "memory", "uptime", "status"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for hostname, task_result in results.items():
                if not task_result.failed:
                    metrics = task_result[0].result
                else:
                    metrics = {"cpu": "ERROR", "memory": "ERROR", "uptime": "ERROR"}

                row = {
                    "hostname": hostname,
                    "timestamp": datetime.now().isoformat(),
                    "cpu": metrics.get("cpu", "N/A") if isinstance(metrics, dict) else metrics,
                    "memory": metrics.get("memory", "N/A") if isinstance(metrics, dict) else "ERROR",
                    "uptime": metrics.get("uptime", "N/A") if isinstance(metrics, dict) else "ERROR",
                    "status": "failed" if task_result.failed else "success",
                }
                writer.writerow(row)

        logger.info(f"Results exported to {output_file}")

    except Exception as e:
        logger.error(f"Error exporting to CSV: {e}")
        raise


def print_table(results: Dict) -> None:
    """Print results in a formatted table."""
    print(f"\n{'Device':<20} {'CPU':<30} {'Memory':<30} {'Uptime':<30}")
    print("=" * 110)

    for hostname, task_result in results.items():
        if not task_result.failed:
            metrics = task_result[0].result
            cpu = metrics.get("cpu", "N/A")[:29]
            memory = metrics.get("memory", "N/A")[:29]
            uptime = metrics.get("uptime", "N/A")[:29]
        else:
            cpu = memory = uptime = "ERROR"

        print(f"{hostname:<20} {cpu:<30} {memory:<30} {uptime:<30}")


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Collect health metrics from network devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-u", "--username", required=True, help="Username for device login")
    parser.add_argument("-p", "--password", required=True, help="Password for device login")
    parser.add_argument(
        "-i",
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "-d",
        "--devices",
        action="append",
        help="Specific device(s) to target (can be used multiple times)",
    )
    parser.add_argument(
        "-o",
        "--output",
        choices=["table", "csv"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting device health check automation")

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.devices:
            nr = nr.filter(name__in=args.devices)

        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password

        logger.info(f"Collecting metrics from {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=get_health_metrics)

        if args.output == "csv":
            export_to_csv(results, "health_metrics.csv")
        else:
            print_table(results)

        failed_count = sum(1 for r in results.values() if r.failed)
        success_count = len(results) - failed_count

        logger.info(f"Health check completed: {success_count} successful, {failed_count} failed")

        sys.exit(0 if failed_count == 0 else 1)

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```