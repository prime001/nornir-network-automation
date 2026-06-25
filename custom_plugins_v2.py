```python
#!/usr/bin/env python3
"""
Device Inventory and Health Report Script

Purpose:
    Gathers device facts, system information, and connectivity status from
    network devices using Nornir and NAPALM. Generates a consolidated report
    of device inventory (model, serial number, OS version, uptime).

Usage:
    python device_health_report.py --devices all --format table
    python device_health_report.py --devices router1,router2 --format csv --output inventory.csv

Prerequisites:
    - Nornir inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
    - Device SSH connectivity with credentials
    - NAPALM library installed: pip install napalm
    - Devices must support NAPALM get_facts call
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def gather_device_facts(task):
    """Retrieve device facts using NAPALM get_facts."""
    try:
        result = task.run(napalm_get, getters=["facts"])
        return result[0].result.get("facts", {})
    except Exception as e:
        logger.warning(f"{task.host.name}: Failed to gather facts - {e}")
        return None


def format_uptime(seconds):
    """Convert uptime seconds to human-readable format."""
    if not seconds:
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def format_table_report(devices_data):
    """Format report as ASCII table."""
    lines = ["\n" + "=" * 110]
    lines.append(
        f"{'Hostname':<20} {'Model':<25} {'Serial':<20} "
        f"{'OS Version':<15} {'Uptime':<15}"
    )
    lines.append("=" * 110)

    for hostname in sorted(devices_data.keys()):
        info = devices_data[hostname]
        if info is None:
            lines.append(f"{hostname:<20} {'UNREACHABLE':<25}")
        else:
            lines.append(
                f"{info.get('hostname', hostname):<20} "
                f"{info.get('model', 'N/A'):<25} "
                f"{info.get('serial_number', 'N/A'):<20} "
                f"{info.get('os_version', 'N/A'):<15} "
                f"{format_uptime(info.get('uptime_seconds', 0)):<15}"
            )

    lines.append("=" * 110 + "\n")
    return "\n".join(lines)


def format_csv_report(devices_data):
    """Format report as CSV."""
    lines = ["Hostname,Model,Serial,OS Version,Uptime,Vendor,Status"]

    for hostname in sorted(devices_data.keys()):
        info = devices_data[hostname]
        if info is None:
            lines.append(f"{hostname},,,,,,UNREACHABLE")
        else:
            uptime = format_uptime(info.get("uptime_seconds", 0))
            lines.append(
                f"{info.get('hostname', hostname)},"
                f"{info.get('model', '')},"
                f"{info.get('serial_number', '')},"
                f"{info.get('os_version', '')},"
                f"{uptime},"
                f"{info.get('vendor', '')},OK"
            )

    return "\n".join(lines)


def format_json_report(devices_data):
    """Format report as JSON."""
    return json.dumps(devices_data, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d",
        "--devices",
        type=str,
        default="all",
        help="Target devices: comma-separated list or 'all' (default: all)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        logger.info("Initializing Nornir inventory...")
        nr = InitNornir(config_file="config.yaml")
        logger.info(f"Loaded {len(nr.inventory.hosts)} hosts from inventory")

        if args.devices != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} target devices")

        logger.info("Gathering device facts (this may take a moment)...")
        results = nr.run(task=gather_device_facts)

        devices_data = {}
        for hostname in results.keys():
            task_result = results[hostname]
            if task_result.failed:
                logger.warning(f"{hostname}: Task failed")
                devices_data[hostname] = None
            else:
                facts = task_result[0].result
                devices_data[hostname] = facts
                logger.debug(f"{hostname}: Successfully gathered facts")

        if args.format == "table":
            output = format_table_report(devices_data)
        elif args.format == "csv":
            output = format_csv_report(devices_data)
        else:
            output = format_json_report(devices_data)

        if args.output:
            Path(args.output).write_text(output)
            logger.info(f"Report written to {args.output}")
        else:
            print(output)

    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```