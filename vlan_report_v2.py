```python
"""
Device Health Check and Uptime Monitoring

Purpose:
    Collects device uptime and system information from network devices using NAPALM.
    Generates a formatted report of device health metrics including uptime, OS version,
    and hardware model information.

Usage:
    python device_health.py --device core-router-01
    python device_health.py --all --format json
    python device_health.py --all --format table

Prerequisites:
    - nornir, netmiko, napalm installed
    - Nornir inventory with device credentials configured
    - Network devices with SSH access enabled
    - NAPALM drivers available for target device types
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def collect_device_health(task) -> Dict[str, Any]:
    """Retrieve device facts and format health data."""
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result.get("facts", {})

        uptime_sec = facts.get("uptime", 0)
        days = uptime_sec // 86400
        hours = (uptime_sec % 86400) // 3600

        return {
            "device": task.host.name,
            "status": "UP",
            "uptime_seconds": uptime_sec,
            "uptime": f"{days}d {hours}h",
            "os_version": facts.get("os_version", "N/A"),
            "model": facts.get("model", "N/A"),
            "serial": facts.get("serial_number", "N/A"),
        }
    except Exception as e:
        logger.error(f"Error collecting health from {task.host.name}: {str(e)}")
        return {
            "device": task.host.name,
            "status": "DOWN",
            "error": str(e),
        }


def format_table(devices: List[Dict[str, Any]]) -> str:
    """Format output as ASCII table."""
    header = (
        f"{'Device':<20} {'Status':<8} {'Uptime':<12} "
        f"{'Model':<20} {'OS Version':<15}"
    )
    separator = "-" * 75

    lines = [separator, header, separator]
    for dev in devices:
        if dev["status"] == "UP":
            lines.append(
                f"{dev['device']:<20} {dev['status']:<8} {dev['uptime']:<12} "
                f"{dev['model']:<20} {dev['os_version']:<15}"
            )
        else:
            error_msg = dev.get("error", "Unknown")[:15]
            lines.append(
                f"{dev['device']:<20} {dev['status']:<8} {'N/A':<12} "
                f"{'N/A':<20} {error_msg:<15}"
            )
    lines.append(separator)

    return "\n".join(lines)


def format_json(devices: List[Dict[str, Any]]) -> str:
    """Format output as JSON."""
    return json.dumps(devices, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Collect and display device health information"
    )
    parser.add_argument(
        "--device",
        help="Target device hostname"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all devices in inventory"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("inventory"),
        help="Path to Nornir inventory directory"
    )

    args = parser.parse_args()

    if not args.device and not args.all:
        parser.error("Specify --device or --all")

    if not args.inventory.exists():
        logger.error(f"Inventory path not found: {args.inventory}")
        return 1

    try:
        nr = InitNornir(config_file=str(args.inventory / "config.yaml"))
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")
    except Exception as e:
        logger.error(f"Failed to load inventory: {e}")
        return 1

    if args.device:
        nr = nr.filter(F(name=args.device))
        if not nr.inventory.hosts:
            logger.error(f"Device not found: {args.device}")
            return 1

    logger.info(f"Collecting health data from {len(nr.inventory.hosts)} device(s)")
    results = nr.run(task=collect_device_health)

    devices = []
    for host_name, host_result in results.items():
        if host_result and len(host_result) > 0:
            task_result = host_result[0]
            if task_result.result:
                devices.append(task_result.result)

    output = format_json(devices) if args.format == "json" else format_table(devices)
    print(output)

    up_count = sum(1 for d in devices if d["status"] == "UP")
    logger.info(f"Collection complete: {up_count}/{len(devices)} devices UP")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```