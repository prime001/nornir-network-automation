```python
"""
NTP Synchronization Status Validator for network device inventory.

Connects to network devices and validates NTP synchronization status,
identifying unsynchronized systems and potential clock skew issues.
Useful for ensuring accurate timestamps across infrastructure.

Usage:
    python ntp_sync_validator.py -i inventory.yaml -u admin -p password

Prerequisites:
    - Nornir installed with netmiko connector
    - Device inventory file with SSH connectivity configured
    - Network devices must support 'show ntp status' command
    - SSH credentials with privilege to execute show commands
"""

import argparse
import logging
import sys

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def validate_ntp_sync(task: Task, warn_offset: float = 100.0) -> Result:
    """
    Check NTP synchronization status on a device.
    
    Returns synchronized state, stratum, clock offset, and any issues.
    """
    try:
        cmd_result = task.run(
            netmiko_send_command,
            command_string='show ntp status'
        )
        
        if cmd_result[0].failed:
            return Result(
                host=task.host,
                failed=True,
                result={'error': 'Failed to retrieve NTP status'}
            )
        
        output = cmd_result[0].result
        if not output:
            return Result(
                host=task.host,
                failed=True,
                result={'error': 'Empty response from device'}
            )
        
        synchronized = 'synchronized' in output.lower() and \
                      'yes' in output.lower()
        stratum = None
        offset = None
        
        for line in output.split('\n'):
            line = line.strip()
            if 'stratum' in line.lower():
                try:
                    stratum = int(line.split()[-1])
                except (ValueError, IndexError):
                    pass
            if 'offset' in line.lower():
                try:
                    offset = float(line.split()[-2])
                except (ValueError, IndexError):
                    pass
        
        issues = []
        if not synchronized:
            issues.append('Device not synchronized to NTP')
        if stratum and stratum > 10:
            issues.append(f'Stratum too high: {stratum}')
        if offset and abs(offset) > warn_offset:
            issues.append(f'Clock offset: {offset:.2f}ms')
        
        return Result(
            host=task.host,
            result={
                'synchronized': synchronized,
                'stratum': stratum,
                'offset_ms': offset,
                'issues': issues,
            }
        )
        
    except Exception as e:
        logger.error(f'{task.host.name}: {type(e).__name__}: {e}')
        return Result(
            host=task.host,
            failed=True,
            result={'error': str(e)}
        )


def main():
    parser = argparse.ArgumentParser(
        description='Validate NTP synchronization across network devices'
    )
    parser.add_argument(
        '-i', '--inventory',
        required=True,
        help='Path to Nornir inventory file'
    )
    parser.add_argument(
        '-u', '--username',
        required=True,
        help='Device SSH username'
    )
    parser.add_argument(
        '-p', '--password',
        required=True,
        help='Device SSH password'
    )
    parser.add_argument(
        '-w', '--warn-offset',
        type=float,
        default=100.0,
        help='Warning threshold for clock offset in milliseconds (default: 100)'
    )
    parser.add_argument(
        '--filter',
        help='Optional: filter devices by name pattern'
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.filter:
            nr = nr.filter(name__contains=args.filter)
        
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password
        
        device_count = len(nr.inventory.hosts)
        logger.info(f'Validating NTP sync on {device_count} device(s)')
        
        results = nr.run(
            task=validate_ntp_sync,
            warn_offset=args.warn_offset,
            name='NTP Synchronization Validator'
        )
        
        print('\n' + '='*70)
        print('NTP SYNCHRONIZATION STATUS REPORT')
        print('='*70)
        
        synced_count = 0
        issue_count = 0
        failed_count = 0
        
        for host_name, task_results in results.items():
            task_result = task_results[0]
            result_data = task_result.result
            
            if task_result.failed:
                status = '❌ ERROR'
                failed_count += 1
                error_msg = result_data.get('error', 'Unknown error')
                print(f'{host_name}: {status} - {error_msg}')
            elif result_data.get('issues'):
                status = '⚠️  WARNING'
                issue_count += 1
                issues_str = '; '.join(result_data['issues'])
                print(f'{host_name}: {status} - {issues_str}')
            else:
                status = '✅ OK'
                synced_count += 1
                stratum = result_data.get('stratum', 'N/A')
                offset = result_data.get('offset_ms')
                offset_str = f' (offset: {offset:.2f}ms)' if offset else ''
                print(f'{host_name}: {status} - Stratum {stratum}{offset_str}')
        
        print('='*70)
        print(f'Summary: {synced_count} synchronized, '
              f'{issue_count} with issues, {failed_count} failed')
        print('='*70 + '\n')
        
        return 0 if failed_count == 0 else 1
        
    except Exception as e:
        logger.error(f'Fatal error: {e}', exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
```