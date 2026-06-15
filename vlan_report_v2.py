```python
"""
Device Health Reporter

Purpose:
    Gathers and reports system health metrics (uptime, CPU, memory, temperature)
    from network devices using NAPALM operational methods.

Usage:
    python device_health_report.py --devices router1,router2 --output json
    python device_health_report.py --filter os:iosxe --verbose

Prerequisites:
    - nornir with netmiko and napalm plugins installed
    - inventory.yaml configured with device credentials
    - devices must support NAPALM get_environment() and get_facts()
    - SSH/NETCONF connectivity to target devices
"""

import argparse
import json
import logging
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(verbose: bool = False) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=level
    )


def get_device_health(task) -> Dict[str, Any]:
    """Retrieve health metrics from a single device."""
    try:
        facts = task.run(
            napalm_get,
            getters=['get_facts', 'get_environment']
        )
        return facts.result
    except Exception as e:
        logging.error(f"Failed to retrieve health for {task.host.name}: {str(e)}")
        return None


def format_health_report(device_name: str, health_data: Dict) -> Dict[str, Any]:
    """Extract and format relevant health metrics."""
    if not health_data or 'get_facts' not in health_data:
        return {
            'device': device_name,
            'status': 'failed',
            'error': 'No data retrieved'
        }

    facts = health_data.get('get_facts', {})
    env = health_data.get('get_environment', {})

    report = {
        'device': device_name,
        'os_version': facts.get('os_version', 'unknown'),
        'uptime_seconds': facts.get('uptime_seconds', 0),
        'serial_number': facts.get('serial_number', 'unknown'),
    }

    if env:
        cpu_data = env.get('cpu', {})
        if isinstance(cpu_data, dict):
            cpu_util = next(iter(cpu_data.values()), {}).get('%usage', 0)
            report['cpu_usage_percent'] = cpu_util

        memory_data = env.get('memory', {})
        if memory_data:
            report['memory_available_mb'] = memory_data.get('available_ram', 0)
            report['memory_used_mb'] = memory_data.get('used_ram', 0)

        temp_data = env.get('temperature', {})
        if temp_data:
            temps = [v.get('temperature', 0) for v in temp_data.values() if isinstance(v, dict)]
            if temps:
                report['max_temp_celsius'] = max(temps)

    report['status'] = 'ok'
    return report


def print_text_report(reports: List[Dict]) -> None:
    """Print human-readable report."""
    for report in reports:
        print(f"\n{report['device']} ({report['os_version']})")
        print(f"  Status: {report['status']}")
        if report['status'] == 'ok':
            uptime_hours = report.get('uptime_seconds', 0) // 3600
            print(f"  Uptime: {uptime_hours} hours")
            print(f"  CPU Usage: {report.get('cpu_usage_percent', 'N/A')}%")
            mem_used = report.get('memory_used_mb', 0)
            mem_avail = report.get('memory_available_mb', 0)
            print(f"  Memory: {mem_used}/{mem_avail} MB")
            if 'max_temp_celsius' in report:
                print(f"  Max Temperature: {report['max_temp_celsius']}°C")
        else:
            print(f"  Error: {report.get('error', 'Unknown error')}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Generate device health reports from network inventory'
    )
    parser.add_argument(
        '--devices',
        help='Comma-separated list of device names to query'
    )
    parser.add_argument(
        '--filter',
        help='Filter devices by key:value (e.g., os:iosxe, site:dc1)'
    )
    parser.add_argument(
        '--output',
        choices=['text', 'json'],
        default='text',
        help='Output format (default: text)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        nr = InitNornir(config_file='config.yaml')
    except Exception as e:
        logging.error(f"Failed to initialize Nornir: {str(e)}")
        return 1

    if args.devices:
        device_list = [d.strip() for d in args.devices.split(',')]
        nr = nr.filter(F(name__in=device_list))
    elif args.filter:
        try:
            key, value = args.filter.split(':')
            nr = nr.filter(**{key: value})
        except ValueError:
            logging.error('Filter must be in format key:value')
            return 1

    if len(nr.inventory.hosts) == 0:
        logging.warning('No devices matched filter criteria')
        return 1

    logging.info(f'Querying health metrics for {len(nr.inventory.hosts)} devices')

    results = nr.run(task=get_device_health)
    reports = []

    for device_name, task_result in results.items():
        if task_result[0].result:
            report = format_health_report(device_name, task_result[0].result)
            reports.append(report)

    if args.output == 'json':
        print(json.dumps(reports, indent=2))
    else:
        print_text_report(reports)

    return 0


if __name__ == '__main__':
    exit(main())
```