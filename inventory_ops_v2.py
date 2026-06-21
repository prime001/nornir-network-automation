```python
"""
Device Software Inventory and Version Audit Tool.

Purpose:
    Audits software versions and OS information across network devices.
    Identifies devices running outdated or non-standard OS versions
    and generates a compliance report.

Usage:
    python software_audit.py -i inventory.yaml -u admin -p password
    python software_audit.py -i inventory.yaml -u admin -p password --output versions.csv
    python software_audit.py -i inventory.yaml -u admin -p password --min-version 15.2

Prerequisites:
    - nornir with network device connectivity
    - SSH access to managed devices
    - Device inventory in YAML format
    - NAPALM or netmiko for device interaction

Environment Variables:
    NORNIR_INVENTORY: Path to inventory file
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.napalm_utils import napalm_get_facts


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_version(version_string: str) -> Optional[tuple]:
    """Extract version numbers from version string."""
    try:
        parts = version_string.split('.')
        return tuple(int(p) for p in parts[:3])
    except (ValueError, IndexError, AttributeError):
        return None


def audit_device_software(task: Task, min_version: str = None) -> Result:
    """Audit device software version and OS information."""
    device = task.host
    audit_data = {
        'device': device.name,
        'ip': device.host,
        'device_type': device.get('device_type', 'unknown'),
        'os_version': None,
        'os': None,
        'model': None,
        'uptime_seconds': None,
        'compliant': True,
        'compliance_notes': [],
        'error': None,
        'timestamp': datetime.now().isoformat()
    }

    try:
        facts_result = task.run(
            napalm_get_facts,
            name='get_facts'
        )

        if facts_result[0].result:
            facts = facts_result[0].result
            audit_data['os_version'] = facts.get('os_version')
            audit_data['os'] = facts.get('os')
            audit_data['model'] = facts.get('model')
            audit_data['uptime_seconds'] = facts.get('uptime_seconds')

            # Version compliance check
            if min_version and audit_data['os_version']:
                device_version = extract_version(audit_data['os_version'])
                min_ver = extract_version(min_version)

                if device_version and min_ver:
                    if device_version < min_ver:
                        audit_data['compliant'] = False
                        audit_data['compliance_notes'].append(
                            f"Version {audit_data['os_version']} < "
                            f"minimum {min_version}"
                        )
                        logger.warning(
                            f"{device.name} running outdated version: "
                            f"{audit_data['os_version']}"
                        )

            logger.info(
                f"Device {device.name}: {audit_data['os']} "
                f"{audit_data['os_version']}"
            )

    except Exception as e:
        audit_data['error'] = str(e)
        logger.error(
            f"Failed to audit {device.name}: {str(e)[:80]}"
        )

    return Result(host=task.host, result=audit_data)


def generate_report(
    results: Dict,
    output_file: Optional[str] = None
) -> List[Dict]:
    """Generate software audit report from results."""
    report_data = []

    for device_name, task_results in results.items():
        for task_name, task_result in task_results.items():
            if task_result.result:
                report_data.append(task_result.result)

    if output_file:
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                if report_data:
                    fieldnames = report_data[0].keys()
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(report_data)
            logger.info(f"Report written to {output_file}")
        except IOError as e:
            logger.error(f"Failed to write report: {e}")

    return report_data


def print_summary(report_data: List[Dict]) -> None:
    """Print audit summary to console."""
    if not report_data:
        print("No audit data available")
        return

    print("\n" + "=" * 80)
    print("SOFTWARE INVENTORY AND VERSION AUDIT")
    print("=" * 80)

    compliant = sum(1 for r in report_data if r['compliant'])
    non_compliant = len(report_data) - compliant

    print(
        f"Total Devices: {len(report_data)} | "
        f"Compliant: {compliant} | Non-Compliant: {non_compliant}"
    )
    print("=" * 80)

    print(f"\n{'Device':<20} {'IP':<15} {'OS':<12} {'Version':<12} {'Status':<10}")
    print("-" * 80)

    for item in sorted(report_data, key=lambda x: x['device']):
        status = "✓ OK" if item['compliant'] else "✗ FAIL"
        os_type = item['os'][:10] if item['os'] else "Unknown"
        version = item['os_version'][:10] if item['os_version'] else "Unknown"

        print(
            f"{item['device']:<20} {item['ip']:<15} "
            f"{os_type:<12} {version:<12} {status:<10}"
        )

        if item['compliance_notes']:
            for note in item['compliance_notes']:
                print(f"  └─ {note}")

        if item['error']:
            print(f"  └─ ERROR: {item['error'][:60]}")

    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Audit software versions across network devices'
    )
    parser.add_argument(
        '-i', '--inventory',
        required=True,
        help='Path to nornir inventory file'
    )
    parser.add_argument(
        '-u', '--username',
        required=True,
        help='Device username'
    )
    parser.add_argument(
        '-p', '--password',
        required=True,
        help='Device password'
    )
    parser.add_argument(
        '--min-version',
        help='Minimum required OS version (e.g., 15.2.4)'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output CSV file for detailed report'
    )
    parser.add_argument(
        '-d', '--devices',
        help='Comma-separated device names to audit'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not Path(args.inventory).exists():
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.devices:
            device_list = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(name__in=device_list)

        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password

        logger.info(
            f"Auditing software on {len(nr.inventory.hosts)} devices"
        )

        results = nr.run(
            task=audit_device_software,
            min_version=args.min_version
        )

        report_data = generate_report(results, args.output)
        print_summary(report_data)

        non_compliant = sum(1 for r in report_data if not r['compliant'])
        sys.exit(1 if non_compliant > 0 else 0)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```