```python
#!/usr/bin/env python3
"""
Device Health Status Reporter.

Purpose:
    Gathers and reports on device health metrics including uptime, CPU
    utilization, interface status across a network inventory using nornir.

Usage:
    python 012_device_health_status.py -i ./inventory -g core-routers
    python 012_device_health_status.py -d router01 -v

Prerequisites:
    - nornir >= 3.0
    - nornir_netmiko or nornir_napalm
    - Network devices accessible via SSH with valid credentials configured
      in inventory/hosts.yaml and inventory/group_vars
"""

import argparse
import logging
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_health(task):
    """Collect device health metrics: uptime, CPU, interfaces."""
    host = task.host.name
    metrics = {'device': host, 'status': 'down'}

    try:
        r1 = task.run(netmiko_send_command, command_string='show version')
        r2 = task.run(netmiko_send_command, command_string='show processes cpu')
        r3 = task.run(netmiko_send_command, command_string='show interfaces brief')

        metrics['status'] = 'up'
        metrics['uptime'] = parse_uptime(r1.result)
        metrics['cpu'] = parse_cpu(r2.result)
        metrics['interfaces'] = count_interfaces(r3.result)
        metrics['health'] = calculate_score(metrics)

    except Exception as e:
        logger.error(f"{host}: {e}")
        metrics['error'] = str(e)

    return metrics


def parse_uptime(output):
    """Extract uptime information from show version output."""
    for line in output.split('\n'):
        if 'uptime' in line.lower():
            return line.strip()
    return 'unknown'


def parse_cpu(output):
    """Extract CPU usage percentage from show processes cpu output."""
    for line in output.split('\n'):
        if '%' in line:
            try:
                return float(line.split()[-1].replace('%', ''))
            except (ValueError, IndexError):
                pass
    return 0.0


def count_interfaces(output):
    """Count up/down interfaces from show interfaces brief output."""
    lines = [
        l for l in output.split('\n')
        if l.strip() and ('up' in l or 'down' in l)
    ]
    up_count = sum(1 for l in lines if l.rstrip().endswith('up'))
    down_count = sum(1 for l in lines if l.rstrip().endswith('down'))
    return {'up': up_count, 'down': down_count}


def calculate_score(metrics):
    """Calculate device health score (0-100) based on collected metrics."""
    if metrics['status'] == 'down':
        return 0

    score = 100
    cpu = metrics.get('cpu', 0)
    if cpu > 80:
        score -= 30
    elif cpu > 60:
        score -= 15

    interfaces = metrics.get('interfaces', {})
    score -= interfaces.get('down', 0) * 10

    return max(0, score)


def main():
    """Main entry point for device health checker."""
    parser = argparse.ArgumentParser(
        description='Collect and report device health metrics'
    )
    parser.add_argument(
        '-i', '--inventory-dir',
        default='./inventory',
        help='Path to nornir inventory directory'
    )
    parser.add_argument(
        '-g', '--groups',
        help='Filter by device groups (comma-separated)'
    )
    parser.add_argument(
        '-d', '--device',
        help='Target specific device by name'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=f'{args.inventory_dir}/config.yaml')

        if args.device:
            nr = nr.filter(name=args.device)
        elif args.groups:
            groups = [g.strip() for g in args.groups.split(',')]
            nr = nr.filter(F(groups__contains=groups))

        logger.info(f"Checking {len(nr.inventory.hosts)} devices")
        results = nr.run(task=gather_health)

        print('\n' + '='*75)
        print('DEVICE HEALTH REPORT')
        print('='*75)

        healthy = warning = critical = 0

        for host_name, host_result in results.items():
            metrics = host_result[0].result
            status = metrics.get('status', 'unknown')
            health = metrics.get('health', 0)
            cpu = metrics.get('cpu', 0)
            interfaces = metrics.get('interfaces', {})

            if health >= 80:
                icon = '✓'
                healthy += 1
            elif health >= 50:
                icon = '⚠'
                warning += 1
            else:
                icon = '✗'
                critical += 1

            up_count = interfaces.get('up', 0)
            down_count = interfaces.get('down', 0)

            print(
                f'{icon} {host_name:20} {status:8} score={health:3d} '
                f'cpu={cpu:5.1f}% up={up_count:2d} down={down_count:2d}'
            )

        print('='*75)
        print(
            f'Summary: {healthy} healthy, {warning} warning, {critical} critical'
        )
        print('='*75 + '\n')

    except Exception as e:
        logger.error(f'Fatal error: {e}', exc_info=args.verbose)
        exit(1)


if __name__ == '__main__':
    main()
```