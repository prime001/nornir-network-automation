```python
"""
Device Connectivity Audit - Verify device reachability to critical targets.

This script uses nornir to execute ping tests from network devices to specified
target IPs/hostnames and generates a connectivity matrix. Useful for validating
network paths and diagnosing connectivity issues after topology changes.

Usage:
    python device_connectivity_audit.py -t targets.txt
    python device_connectivity_audit.py -d switch1,switch2 -t 8.8.8.8,1.1.1.1

Prerequisites:
    - nornir configured with inventory (inventory.yaml)
    - Network devices with SSH/netmiko access
    - Target hosts must support ICMP ping or equivalent
"""

import argparse
import logging
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command
from nornir.core.filter import F

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_targets(targets_input):
    """Parse targets from file or comma-separated string."""
    try:
        with open(targets_input) as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return [t.strip() for t in targets_input.split(',') if t.strip()]


def ping_target(task: Task, targets: list) -> Result:
    """Ping targets from device and return reachability status."""
    device = task.host
    results = {}
    
    for target in targets:
        try:
            if device.platform in ['ios', 'iosxe', 'iosxr']:
                cmd = f'ping {target} count 1 timeout 1'
            elif device.platform == 'eos':
                cmd = f'ping {target} -c 1'
            elif device.platform == 'junos':
                cmd = f'request shell execute "ping -c 1 {target}"'
            else:
                cmd = f'ping -c 1 {target}'
            
            output = task.run(netmiko_send_command, command_string=cmd)
            result_text = output[0].result.lower()
            
            is_reachable = ('success' in result_text or 
                           '0% loss' in result_text or 
                           '0% packet loss' in result_text or
                           'received' in result_text)
            
            results[target] = {'reachable': is_reachable}
        except Exception as e:
            logger.warning(f'{device.name} -> {target}: {str(e)[:50]}')
            results[target] = {'reachable': False, 'error': str(e)[:50]}
    
    return Result(host=device, result=results)


def generate_matrix(results, targets):
    """Generate and display connectivity matrix."""
    matrix = {}
    
    for host_name, task_result in results.items():
        if task_result.failed:
            logger.error(f'{host_name}: {task_result.exception}')
            continue
        
        matrix[host_name] = task_result[0].result
    
    print('\n' + '='*80)
    print('CONNECTIVITY AUDIT MATRIX')
    print('='*80)
    print(f"{'Device':<20}", end='')
    for target in targets:
        print(f"{target:<18}", end='')
    print()
    print('-' * (20 + len(targets) * 18))
    
    successful = 0
    total = 0
    
    for device in sorted(matrix.keys()):
        print(f"{device:<20}", end='')
        for target in targets:
            reachable = matrix[device].get(target, {}).get('reachable', False)
            status = '✓ PASS' if reachable else '✗ FAIL'
            print(f"{status:<18}", end='')
            total += 1
            if reachable:
                successful += 1
        print()
    
    print('='*80)
    pct = (successful / total * 100) if total > 0 else 0
    print(f'Summary: {successful}/{total} tests passed ({pct:.0f}%)')
    print('='*80 + '\n')


def main():
    parser = argparse.ArgumentParser(
        description='Audit device connectivity to critical targets'
    )
    parser.add_argument(
        '-i', '--inventory',
        default='inventory.yaml',
        help='Nornir inventory file'
    )
    parser.add_argument(
        '-d', '--devices',
        help='Comma-separated device names to audit'
    )
    parser.add_argument(
        '-t', '--targets',
        required=True,
        help='File with targets (one per line) or comma-separated list'
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.devices:
            nr = nr.filter(name__in=[d.strip() for d in args.devices.split(',')])
        
        targets = parse_targets(args.targets)
        
        if not targets:
            logger.error('No targets specified')
            return 1
        
        logger.info(f'Starting audit: {len(nr.inventory.hosts)} devices, {len(targets)} targets')
        results = nr.run(task=ping_target, targets=targets)
        
        generate_matrix(results, targets)
        
        if results.failed:
            logger.warning(f'{len(results.failed)} device(s) had execution errors')
            return 1
        
        return 0
        
    except Exception as e:
        logger.error(f'Audit failed: {e}', exc_info=True)
        return 1


if __name__ == '__main__':
    exit(main())
```