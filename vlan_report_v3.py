```python
#!/usr/bin/env python
"""
Device Inventory Facts Collector.

Collects comprehensive device inventory information (model, OS version,
serial numbers, uptime) and exports to JSON or CSV format. Useful for
network documentation, asset tracking, and compliance reporting.

Usage:
    python device_facts_collector.py --output inventory.json
    python device_facts_collector.py --format csv --output devices.csv

Prerequisites:
    - Nornir inventory with device definitions
    - NAPALM plugin for device connectivity
    - Valid SSH/CLI credentials configured in Nornir
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get_facts

logger = logging.getLogger("device_facts_collector")


def collect_device_facts(task):
    """Collect facts from a single device using NAPALM."""
    try:
        result = task.run(task=napalm_get_facts)
        if result[0].ok:
            facts = result[0].result
            uptime_seconds = facts.get("uptime_seconds", 0)
            uptime_days = uptime_seconds / (24 * 3600)

            return {
                "hostname": task.host.name,
                "model": facts.get("model", "Unknown"),
                "os_version": facts.get("os_version", "Unknown"),
                "serial_number": facts.get("serial_number", "Unknown"),
                "vendor": facts.get("vendor", "Unknown"),
                "uptime_days": round(uptime_days, 2),
                "interface_count": len(facts.get("interface_list", [])),
                "fqdn": facts.get("fqdn", ""),
                "collected_at": datetime.now().isoformat(),
            }
    except Exception as e:
        logger.error(f"Failed to collect facts from {task.host.name}: {e}")
    return None


def export_json(data, filename):
    """Export inventory data to JSON file."""
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported {len(data)} devices to {filename}")
        return True
    except IOError as e:
        logger.error(f"Failed to write JSON: {e}")
        return False


def export_csv(data, filename):
    """Export inventory data to CSV file."""
    if not data:
        logger.error("No data to export")
        return False

    try:
        fieldnames = data[0].keys()
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
        logger.info(f"Exported {len(data)} devices to {filename}")
        return True
    except IOError as e:
        logger.error(f"Failed to write CSV: {e}")
        return False


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Collect device inventory facts and export results"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output filename (required)"
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format: json or csv (default: json)"
    )
    parser.add_argument(
        "--filter",
        help="Filter devices by attribute (e.g., 'role=core' or 'site=nyc')"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        logger.info("Initializing Nornir...")
        nr = InitNornir(config_file="config.yaml")

        if args.filter:
            try:
                key, value = args.filter.split("=", 1)
                nr = nr.filter(F(**{key: value}))
            except ValueError:
                logger.error("Filter format invalid. Use 'key=value'")
                return 1

        device_count = len(nr.inventory.hosts)
        if device_count == 0:
            logger.error("No devices matched filter criteria")
            return 1

        logger.info(f"Collecting facts from {device_count} devices...")
        results = nr.run(task=collect_device_facts)

        inventory_data = []
        success_count = 0
        failed_count = 0

        for device_name, task_result in results.items():
            if task_result[0].result:
                inventory_data.append(task_result[0].result)
                success_count += 1
            else:
                failed_count += 1

        logger.info(
            f"Collection complete: {success_count} succeeded, "
            f"{failed_count} failed"
        )

        if not inventory_data:
            logger.error("No device facts collected")
            return 1

        if args.format == "csv":
            success = export_csv(inventory_data, args.output)
        else:
            success = export_json(inventory_data, args.output)

        return 0 if success else 1

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```