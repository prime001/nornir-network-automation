```python
#!/usr/bin/env python3
"""
Device Software and License Audit

Collects and reports on software versions, serial numbers, and system information
across network devices using Nornir and Netmiko.

Prerequisites:
  - nornir >= 3.0
  - nornir-netmiko plugin
  - Network devices reachable via SSH
  - Credentials configured in nornir inventory or environment variables
  - Device types supported by netmiko (Cisco, Juniper, Arista, etc.)

Usage:
  Audit all devices:
    python software_audit.py --inventory inventory.yaml

  Audit specific group:
    python software_audit.py --inventory inventory.yaml --group core-devices

  Export results as CSV:
    python software_audit.py --inventory inventory.yaml --export-csv audit.csv

  Audit with custom command:
    python software_audit.py --inventory inventory.yaml --command "show version"
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko.tasks import netmiko_send_command


logger = logging.getLogger(__name__)


def setup_logging(verbosity=0):
    """Configure logging based on verbosity level."""
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def audit_device(task, command):
    """Collect software version info from device."""
    try:
        result = task.run(
            task=netmiko_send_command,
            command_string=command,
            use_textfsm=False
        )
        output = result[0].result

        return {
            'status': 'success',
            'device': task.host.name,
            'device_type': task.host.get('device_type', 'unknown'),
            'output': output,
            'timestamp': datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error auditing {task.host.name}: {e}")
        return {
            'status': 'failed',
            'device': task.host.name,
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
        }


def run_audit(nr, command):
    """Execute software audit across all devices in inventory."""
    results = {}

    for device_name, device_obj in nr.inventory.hosts.items():
        logger.info(f"Auditing {device_name}")
        result = audit_device(device_obj, command)
        results[device_name] = result

    return results


def print_text_report(results):
    """Print audit results in human-readable table format."""
    print(f"\n{'Device':<25} {'Device Type':<15} {'Status':<12}")
    print("-" * 55)

    for device, info in sorted(results.items()):
        status = info['status']
        dev_type = info.get('device_type', 'unknown')
        print(f"{device:<25} {dev_type:<15} {status:<12}")

        if status == 'success':
            output_lines = info['output'].split('\n')[:3]
            for line in output_lines:
                if line.strip():
                    print(f"  {line[:50]}")


def export_json(results, output_file):
    """Export audit results as JSON file."""
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results exported to {output_file}")


def export_csv(results, output_file):
    """Export audit results as CSV file."""
    fieldnames = ['device', 'device_type', 'status', 'timestamp']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for device, info in sorted(results.items()):
            writer.writerow({
                'device': device,
                'device_type': info.get('device_type', 'unknown'),
                'status': info['status'],
                'timestamp': info.get('timestamp', ''),
            })

    logger.info(f"Results exported to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Audit device software versions and system information',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('--inventory', required=True,
                        help='Path to nornir inventory YAML file')
    parser.add_argument('--devices', nargs='+',
                        help='Specific devices to audit (filter by name)')
    parser.add_argument('--group',
                        help='Filter devices by group name')
    parser.add_argument('--command', default='show version',
                        help='Command to execute on devices (default: show version)')
    parser.add_argument('--export-json', metavar='FILE',
                        help='Export results as JSON file')
    parser.add_argument('--export-csv', metavar='FILE',
                        help='Export results as CSV file')
    parser.add_argument('--format', choices=['text', 'json'],
                        default='text', help='Output format (default: text)')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity (-vv for debug)')

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        logger.info("Initializing Nornir")
        nr = InitNornir(config_file=args.inventory)

        if args.devices:
            nr = nr.filter(F(name__in=args.devices))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))

        if not nr.inventory.hosts:
            logger.error("No devices matched selection criteria")
            sys.exit(1)

        logger.info(f"Starting audit for {len(nr.inventory.hosts)} devices")
        results = run_audit(nr, args.command)

        if args.format == 'json':
            print(json.dumps(results, indent=2, default=str))
        else:
            print_text_report(results)

        if args.export_json:
            export_json(results, args.export_json)

        if args.export_csv:
            export_csv(results, args.export_csv)

        success_count = sum(1 for r in results.values() if r['status'] == 'success')
        logger.info(f"Audit complete: {success_count}/{len(results)} devices succeeded")

        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
```