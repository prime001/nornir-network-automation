```python
"""
Device Configuration Compliance Checker

Purpose: Validate network device configurations against a defined compliance policy.
Checks for required configuration items such as NTP, DNS, syslog servers, device
hostnames, and other critical settings that must be present on all managed devices.

Usage:
    python device_config_compliance.py --devices core_routers --policy strict
    python device_config_compliance.py --devices all --policy standard --format json

Prerequisites:
    - nornir installed with netmiko drivers
    - Inventory file configured (hosts.yaml, groups.yaml, defaults.yaml)
    - Network connectivity to target devices
    - Valid credentials in environment or inventory
"""

import argparse
import json
import logging
from typing import Dict, List
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

COMPLIANCE_POLICIES = {
    'strict': {
        'ntp_servers_min': 3,
        'dns_servers_min': 2,
        'syslog_servers_min': 1,
        'hostname_required': True,
    },
    'standard': {
        'ntp_servers_min': 2,
        'dns_servers_min': 1,
        'syslog_servers_min': 1,
        'hostname_required': True,
    },
    'minimal': {
        'ntp_servers_min': 1,
        'dns_servers_min': 1,
        'syslog_servers_min': 0,
        'hostname_required': True,
    },
}


def parse_ios_config(config: str) -> Dict[str, List[str]]:
    """Parse Cisco IOS config for compliance items."""
    items = {
        'ntp_servers': [],
        'dns_servers': [],
        'syslog_servers': [],
        'hostname': None,
    }

    for line in config.split('\n'):
        line = line.strip()
        if line.startswith('hostname '):
            items['hostname'] = line.split()[-1]
        elif line.startswith('ntp server '):
            server = line.split()[-1]
            if server not in items['ntp_servers']:
                items['ntp_servers'].append(server)
        elif line.startswith('ip name-server '):
            server = line.split()[-1]
            if server not in items['dns_servers']:
                items['dns_servers'].append(server)
        elif line.startswith('logging '):
            parts = line.split()
            if len(parts) > 1 and '.' in parts[-1]:
                if parts[-1] not in items['syslog_servers']:
                    items['syslog_servers'].append(parts[-1])

    return items


def check_compliance(task: Task, policy: str) -> Result:
    """Check device configuration compliance against policy."""
    policy_rules = COMPLIANCE_POLICIES.get(policy, COMPLIANCE_POLICIES['standard'])

    try:
        config_result = task.run(
            task=netmiko_send_command,
            command_string='show running-config',
        )
        config_output = config_result[0].result

        config_items = parse_ios_config(config_output)

        violations = []

        if not config_items['hostname']:
            violations.append('Hostname not configured')

        ntp_count = len(config_items['ntp_servers'])
        if ntp_count < policy_rules['ntp_servers_min']:
            violations.append(
                f"NTP servers: {ntp_count} "
                f"(required: {policy_rules['ntp_servers_min']})"
            )

        dns_count = len(config_items['dns_servers'])
        if dns_count < policy_rules['dns_servers_min']:
            violations.append(
                f"DNS servers: {dns_count} "
                f"(required: {policy_rules['dns_servers_min']})"
            )

        syslog_count = len(config_items['syslog_servers'])
        if syslog_count < policy_rules['syslog_servers_min']:
            violations.append(
                f"Syslog servers: {syslog_count} "
                f"(required: {policy_rules['syslog_servers_min']})"
            )

        result = {
            'hostname': config_items['hostname'],
            'compliant': len(violations) == 0,
            'violations': violations,
            'config_items': config_items,
        }

        return Result(host=task.host, result=result)

    except Exception as e:
        logger.error(f'{task.host.name}: {e}')
        return Result(
            host=task.host,
            result={
                'error': str(e),
                'compliant': False,
            },
            failed=True,
        )


def main():
    parser = argparse.ArgumentParser(
        description='Check network device configuration compliance'
    )
    parser.add_argument(
        '--devices',
        default='all',
        help='Device group or individual device name (default: all)'
    )
    parser.add_argument(
        '--policy',
        choices=list(COMPLIANCE_POLICIES.keys()),
        default='standard',
        help='Compliance policy to apply (default: standard)'
    )
    parser.add_argument(
        '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format (default: table)'
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file='config.yaml')

        if args.devices != 'all':
            nr = nr.filter(name=args.devices) or nr.filter(group=args.devices)

        if len(nr.inventory.hosts) == 0:
            logger.warning(f'No devices found: {args.devices}')
            return

        logger.info(f'Running compliance check on {len(nr.inventory.hosts)} devices')
        logger.info(f'Policy: {args.policy}')

        results = nr.run(
            task=check_compliance,
            policy=args.policy,
            num_workers=4,
        )

        compliance_results = {}
        compliant_count = 0

        for host_name, multi_result in results.items():
            for task_result in multi_result:
                if task_result.result:
                    result_data = task_result.result
                    compliance_results[host_name] = result_data
                    if result_data.get('compliant'):
                        compliant_count += 1

        if args.format == 'json':
            print(json.dumps(compliance_results, indent=2))
        else:
            total = len(compliance_results)
            print(f'\n{"Device":<20} {"Status":<12} {"Violations":<50}')
            print('-' * 82)
            for device, data in compliance_results.items():
                if 'error' in data:
                    print(f'{device:<20} {"ERROR":<12} {data["error"]:<50}')
                else:
                    status = 'COMPLIANT' if data['compliant'] else 'VIOLATION'
                    violations_str = '; '.join(data['violations'])[:50] if data['violations'] else 'None'
                    print(f'{device:<20} {status:<12} {violations_str:<50}')

            print(f'\nSummary: {compliant_count}/{total} devices compliant')

        logger.info(f'Compliance check complete: {compliant_count}/{total} compliant')

    except Exception as e:
        logger.error(f'Compliance check failed: {e}', exc_info=True)
        raise


if __name__ == '__main__':
    main()
```