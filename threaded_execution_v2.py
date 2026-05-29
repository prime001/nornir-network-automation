```python
"""
Device Uptime and Facts Report

Collects device facts including uptime, software version, model, and serial number.
Generates a report for network inventory and compliance tracking.

Usage:
    python device_facts_report.py --config-file nornir_config.yaml --output report.csv

Prerequisites:
    - Nornir installed (pip install nornir nornir-napalm)
    - Nornir inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
    - Devices accessible via SSH with appropriate credentials
    - NAPALM driver support for device types

Output:
    CSV or JSON report with device facts including:
    - Device name, hostname, vendor, model
    - Serial number, uptime
    - OS version, configuration status
"""

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get


def setup_logging(level: str = "INFO") -> None:
    """Configure logging to console and file."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, level.upper()),
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("device_facts.log"),
        ],
    )


def collect_device_facts(task: Task) -> Result:
    """Collect device facts using NAPALM get_facts."""
    try:
        result = task.run(napalm_get, getters=["get_facts"])
        return result
    except Exception as e:
        logging.error(f"Error collecting facts from {task.host.name}: {e}")
        return Result(host=task.host, result={}, failed=True)


def parse_facts(task: Task) -> Result:
    """Parse and format device facts from NAPALM output."""
    try:
        facts_result = task.run(napalm_get, getters=["get_facts"])

        if facts_result.failed:
            return Result(
                host=task.host,
                result={"device": task.host.name, "error": "Failed to get facts"},
                failed=True,
            )

        facts = facts_result[0].result.get("get_facts", {})
        uptime_seconds = facts.get("uptime_seconds", 0)
        uptime_days = uptime_seconds // 86400

        parsed = {
            "device": task.host.name,
            "hostname": facts.get("hostname", "N/A"),
            "vendor": facts.get("vendor", "N/A"),
            "model": facts.get("model", "N/A"),
            "serial_number": facts.get("serial_number", "N/A"),
            "uptime_seconds": uptime_seconds,
            "uptime_days": uptime_days,
            "os_version": facts.get("os_version", "N/A"),
            "fqdn": facts.get("fqdn", "N/A"),
            "interface_count": facts.get("interface_count", "N/A"),
        }

        return Result(host=task.host, result=parsed)

    except Exception as e:
        logging.error(f"Error parsing facts from {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={"device": task.host.name, "error": str(e)},
            failed=True,
        )


def write_csv_report(data: List[Dict[str, Any]], output_path: Path) -> None:
    """Write facts to CSV file."""
    if not data:
        logging.warning("No data to write")
        return

    fieldnames = list(data[0].keys())

    try:
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
        logging.info(f"CSV report written to {output_path}")
    except IOError as e:
        logging.error(f"Failed to write CSV file: {e}")
        raise


def write_json_report(data: List[Dict[str, Any]], output_path: Path) -> None:
    """Write facts to JSON file."""
    try:
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"JSON report written to {output_path}")
    except IOError as e:
        logging.error(f"Failed to write JSON file: {e}")
        raise


def display_summary(data: List[Dict[str, Any]]) -> None:
    """Display summary statistics to console."""
    if not data:
        print("No data collected")
        return

    print("\n" + "=" * 90)
    print("DEVICE FACTS SUMMARY")
    print("=" * 90)
    print(f"{'Device':<20} {'Vendor':<10} {'Model':<20} {'Version':<15} {'Days Up':<10}")
    print("-" * 90)

    for record in data:
        if "error" not in record:
            device = record.get("device", "N/A")
            vendor = record.get("vendor", "N/A")
            model = record.get("model", "N/A")
            version = record.get("os_version", "N/A")
            uptime = record.get("uptime_days", 0)
            print(f"{device:<20} {vendor:<10} {model:<20} {version:<15} {uptime:<10}")

    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Collect and report device facts across network"
    )
    parser.add_argument(
        "--config-file",
        default="nornir_config.yaml",
        help="Nornir configuration file path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file path (CSV or JSON based on extension)",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format (overridden by file extension if --output used)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level",
    )

    args = parser.parse_args()
    setup_logging(args.log_level)

    logger = logging.getLogger(__name__)
    logger.info("Starting device facts collection")

    try:
        nr = InitNornir(config_file=args.config_file)
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")

        results = nr.run(task=parse_facts, name="Get Device Facts")

        all_facts = []
        failed_count = 0

        for task_result in results.values():
            if not task_result.failed:
                all_facts.append(task_result[0].result)
            else:
                failed_count += 1
                logger.warning(f"Failed to collect facts from device")

        if args.output:
            output_path = Path(args.output)
            if args.format == "json" or output_path.suffix == ".json":
                write_json_report(all_facts, output_path)
            else:
                write_csv_report(all_facts, output_path)
        else:
            display_summary(all_facts)

        logger.info(
            f"Collection complete: {len(all_facts)} successful, {failed_count} failed"
        )

    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config_file}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```