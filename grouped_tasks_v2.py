```python
"""
Network Device Health Check and Facts Collection

Purpose:
    Collects device facts and validates health status across network devices.
    Gathers hostname, uptime, model, OS version, serial number, and validates
    critical configurations like NTP status.

Usage:
    python device_health_check.py --devices leaf01,leaf02 --username admin --password admin123
    python device_health_check.py --devices all --username admin --password admin123 --check-ntp
    python device_health_check.py --devices all --username admin --password admin123 --json

Prerequisites:
    - nornir and plugins installed (pip install nornir nornir-netmiko nornir-napalm)
    - Devices configured in inventory (hosts.yaml)
    - Network devices accessible via SSH
    - NAPALM driver available for device types

Output:
    - Device facts and health status to stdout (formatted table by default)
    - JSON output with --json flag
    - Device health warnings for critical issues
"""

import argparse
import json
import logging
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get
from nornir.core.filter import F


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_device_health(task: Task, check_ntp: bool = False) -> Result:
    """Validate device health and collect facts using NAPALM."""
    host = task.host
    health_status = {'device': host.name, 'checks': {}, 'warnings': []}

    try:
        facts_result = task.run(
            napalm_get,
            getters=['facts'],
            severity_level=logging.WARNING
        ).result

        device_facts = facts_result.get('facts', {})

        if not device_facts:
            return Result(
                host=host,
                failed=True,
                result="No facts retrieved"
            )

        uptime_seconds = device_facts.get('uptime', 0)
        if uptime_seconds < 3600:
            health_status['warnings'].append('Device uptime < 1 hour')

        uptime_hours = uptime_seconds // 3600
        uptime_days = uptime_hours // 24

        health_status['checks']['hostname'] = device_facts.get('hostname', 'N/A')
        health_status['checks']['model'] = device_facts.get('model', 'N/A')
        health_status['checks']['os_version'] = device_facts.get('os_version', 'N/A')
        health_status['checks']['serial_number'] = device_facts.get('serial_number', 'N/A')
        health_status['checks']['uptime_days'] = uptime_days
        health_status['checks']['interface_count'] = device_facts.get('interface_count', 0)
        health_status['checks']['vendor'] = device_facts.get('vendor', 'N/A')

        if check_ntp:
            try:
                ntp_result = task.run(
                    napalm_get,
                    getters=['ntp'],
                    severity_level=logging.WARNING
                ).result
                ntp_data = ntp_result.get('ntp', {})
                ntp_enabled = ntp_data.get('enabled', False)
                health_status['checks']['ntp_enabled'] = ntp_enabled
                if not ntp_enabled:
                    health_status['warnings'].append('NTP is not enabled')
            except Exception as e:
                logger.debug(f"{host.name}: NTP check unsupported - {e}")

        return Result(host=host, result=health_status)

    except Exception as e:
        logger.error(f"{host.name}: Health check failed - {e}")
        return Result(host=host, failed=True, exception=e)


def format_table_output(results: Dict[str, Any]) -> None:
    """Display results in formatted table."""
    for host_name, task_result in results.items():
        if task_result[0].result:
            health = task_result[0].result
            print(f"\n{'='*70}")
            print(f"Device: {health['device']}")
            print(f"{'='*70}")
            for check, value in health['checks'].items():
                print(f"  {check:<25}: {value}")
            if health['warnings']:
                print(f"\n  ⚠️  Warnings:")
                for warning in health['warnings']:
                    print(f"    - {warning}")
        elif task_result[0].failed:
            print(f"\n❌ {host_name}: Health check failed")
            if task_result[0].exception:
                logger.error(f"Exception: {task_result[0].exception}")


def format_json_output(results: Dict[str, Any]) -> None:
    """Display results in JSON format."""
    output = {}
    for host_name, task_result in results.items():
        if task_result[0].result:
            output[host_name] = task_result[0].result
        else:
            output[host_name] = {'failed': True, 'error': str(task_result[0].exception)}
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description='Collect device facts and validate network device health'
    )
    parser.add_argument(
        '--devices',
        required=True,
        help='Comma-separated device names or "all" for all devices'
    )
    parser.add_argument(
        '--username',
        required=True,
        help='Device username'
    )
    parser.add_argument(
        '--password',
        required=True,
        help='Device password'
    )
    parser.add_argument(
        '--check-ntp',
        action='store_true',
        help='Include NTP configuration validation'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output results in JSON format'
    )
    parser.add_argument(
        '--inventory',
        default='inventory',
        help='Path to nornir inventory directory'
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")

        if args.devices.lower() != 'all':
            device_list = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(F(name__in=device_list))

        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password

        logger.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")

        results = nr.run(task=validate_device_health, check_ntp=args.check_ntp)

        if args.json:
            format_json_output(results)
        else:
            format_table_output(results)

        logger.info("Health check completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```