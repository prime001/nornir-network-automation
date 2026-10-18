```python
"""
Device Uptime and Health Statistics Collector.

Collects system information, uptime, and interface statistics from network devices
using Nornir. Generates a health report with device status and error summary.

Prerequisites:
    nornir, nornir-netmiko, nornir-utils

Usage:
    python 071_device_health_collector.py -i inventory.yaml
    python 071_device_health_collector.py -i inventory.yaml -d switch-01 -e csv
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def collect_health_stats(task) -> Dict[str, Any]:
    """Collect system uptime and interface error statistics from device."""
    device = task.host.name
    device_type = task.host.get("device_type", "").lower()
    stats = {
        "device": device,
        "timestamp": datetime.now().isoformat(),
        "uptime": "N/A",
        "version": "N/A",
        "int_count": 0,
        "int_errors": 0,
        "status": "ok",
    }

    try:
        if "cisco" in device_type or not device_type:
            result = task.run(
                netmiko_send_command,
                command_string="show version",
            )
            output = result[0].result if result else ""

            for line in output.split("\n"):
                if "uptime" in line.lower():
                    stats["uptime"] = line.strip()
                if "software" in line.lower() or "version" in line.lower():
                    stats["version"] = line.strip()

            err_result = task.run(
                netmiko_send_command,
                command_string="show interfaces | include (errors|discards)",
            )
            error_output = err_result[0].result if err_result else ""
            error_lines = [l.strip() for l in error_output.split("\n") if l.strip()]
            stats["int_errors"] = len(error_lines)

            int_result = task.run(
                netmiko_send_command,
                command_string="show interfaces | include protocol",
            )
            if int_result:
                int_output = int_result[0].result
                stats["int_count"] = int_output.count("is up")

        logger.info(f"✓ {device}: uptime={stats['uptime'][:20]}, "
                    f"errors={stats['int_errors']}")
        task.host["health_data"] = stats

    except Exception as e:
        logger.error(f"✗ {device}: {str(e)[:60]}")
        stats["status"] = "error"
        stats["uptime"] = str(e)[:50]
        task.host["health_data"] = stats

    return stats


def export_report(nr, export_format: str, output_file: str = None) -> None:
    """Export health statistics in specified format."""
    results = []

    for host in nr.inventory.hosts.values():
        if hasattr(host, "health_data"):
            results.append(host.health_data)

    if not results:
        print("No results collected")
        return

    if export_format == "json":
        output = json.dumps(results, indent=2)
        if output_file:
            with open(output_file, "w") as f:
                f.write(output)
            logger.info(f"Report saved to {output_file}")
        else:
            print(output)

    elif export_format == "csv":
        if not output_file:
            output_file = "device_health_report.csv"
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "device",
                    "timestamp",
                    "uptime",
                    "version",
                    "int_count",
                    "int_errors",
                    "status",
                ],
            )
            writer.writeheader()
            writer.writerows(results)
        logger.info(f"Report saved to {output_file}")

    else:
        for r in results:
            print(f"\n{r['device']} ({r['status']})")
            print(f"  Uptime:  {r['uptime'][:40]}")
            print(f"  Interfaces: {r['int_count']} total, "
                  f"{r['int_errors']} with errors")


def main():
    """Execute health check collection."""
    parser = argparse.ArgumentParser(
        description="Collect device health statistics"
    )
    parser.add_argument(
        "-i", "--inventory", required=True, help="Nornir inventory file"
    )
    parser.add_argument("-d", "--device", help="Target device (default: all)")
    parser.add_argument("-g", "--group", help="Target group")
    parser.add_argument(
        "-e",
        "--export",
        choices=["text", "json", "csv"],
        default="text",
        help="Export format",
    )
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices")

        if args.device:
            nr = nr.filter(F(name=args.device))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))

        logger.info(f"Collecting health stats from {len(nr.inventory.hosts)} "
                    "device(s)")
        nr.run(task=collect_health_stats, num_workers=4)

        export_report(nr, args.export, args.output)
        logger.info("Complete")

    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```