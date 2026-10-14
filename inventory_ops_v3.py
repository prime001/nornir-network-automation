```python
#!/usr/bin/env python3
"""
Device Reachability and Facts Monitor

Purpose:
    Monitor network device reachability and collect operational facts (uptime,
    OS version, serial number, model). Identifies unreachable devices and
    generates a comprehensive status report.

Usage:
    python device_monitor.py --group core
    python device_monitor.py --device switch1 --verbose
    python device_monitor.py --output report.csv

Prerequisites:
    - nornir with netmiko and napalm installed
    - Inventory directory structure with config.yaml, hosts.yaml, groups.yaml
    - Valid device credentials in inventory
"""

import argparse
import csv
import logging
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get

logger = logging.getLogger(__name__)


def setup_logging(verbose=False):
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)


def get_device_facts(task):
    """Retrieve device facts using NAPALM."""
    try:
        result = task.run(napalm_get, getters=['facts'])
        facts = result[0].result.get('facts', {})

        return {
            'device': task.host.name,
            'vendor': facts.get('vendor', 'unknown'),
            'model': facts.get('model', 'unknown'),
            'os_version': facts.get('os_version', 'unknown'),
            'serial': facts.get('serial_number', 'unknown'),
            'uptime_seconds': facts.get('uptime_seconds', 0),
            'status': 'reachable',
            'error': None
        }
    except Exception as e:
        logger.error(f"Failed to retrieve facts from {task.host.name}: {e}")
        return {
            'device': task.host.name,
            'vendor': 'unknown',
            'model': 'unknown',
            'os_version': 'unknown',
            'serial': 'unknown',
            'uptime_seconds': 0,
            'status': 'unreachable',
            'error': str(e)[:60]
        }


def format_uptime(seconds):
    """Convert uptime seconds to human-readable format."""
    if not isinstance(seconds, int) or seconds == 0:
        return 'N/A'

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def generate_report(results):
    """Generate status report from results."""
    devices = []
    reachable = 0
    unreachable = 0

    for host_name, multi_result in results.items():
        if multi_result.failed:
            data = {
                'device': host_name,
                'status': 'task_failed',
                'vendor': 'unknown',
                'model': 'unknown',
                'os_version': 'unknown',
                'serial': 'unknown',
                'uptime_seconds': 0,
                'error': 'Task execution failed'
            }
            unreachable += 1
        else:
            data = multi_result[0].result
            if data['status'] == 'reachable':
                reachable += 1
            else:
                unreachable += 1

        devices.append(data)

    return devices, reachable, unreachable


def write_csv(devices, filepath):
    """Export results to CSV file."""
    fieldnames = [
        'device', 'status', 'vendor', 'model', 'os_version',
        'serial', 'uptime_seconds', 'error'
    ]

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(devices)

    logger.info(f"Results exported to {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor device reachability and collect operational facts'
    )
    parser.add_argument(
        '--inventory',
        default='./inventory',
        help='Path to nornir inventory directory'
    )
    parser.add_argument(
        '--device',
        help='Target specific device by name'
    )
    parser.add_argument(
        '--group',
        help='Target devices in a specific group'
    )
    parser.add_argument(
        '--output',
        help='Export results to CSV file'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")

        if args.device:
            nr = nr.filter(F(name=args.device))
            logger.info(f"Targeting device: {args.device}")
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
            logger.info(f"Targeting group: {args.group}")

        device_count = len(nr.inventory.hosts)
        if device_count == 0:
            logger.error("No devices matched filter criteria")
            return 1

        logger.info(f"Monitoring {device_count} device(s)...")

        results = nr.run(task=get_device_facts)
        devices, reachable, unreachable = generate_report(results)

        # Print report header
        print(f"\n{'=' * 90}")
        print(f"Device Status Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'=' * 90}")
        print(f"Reachable: {reachable} | Unreachable: {unreachable}")
        print(f"{'-' * 90}")
        print(f"{'Device':<20} {'Status':<12} {'Model':<15} {'OS':<12} {'Uptime':<15}")
        print(f"{'-' * 90}")

        for device in devices:
            uptime_str = format_uptime(device['uptime_seconds'])
            print(
                f"{device['device']:<20} {device['status']:<12} "
                f"{device['model']:<15} {device['os_version']:<12} {uptime_str:<15}"
            )

        print(f"{'=' * 90}\n")

        if args.output:
            write_csv(devices, args.output)

        return 0 if unreachable == 0 else 1

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 2


if __name__ == "__main__":
    exit(main())
```