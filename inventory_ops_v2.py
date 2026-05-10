```python
"""
Device Health Monitoring

Monitor CPU, memory, and temperature metrics across network devices.

Usage:
    python device_health_monitor.py -i inventory.yaml -d router1,router2
    python device_health_monitor.py -i inventory.yaml --group core_devices
    python device_health_monitor.py -i inventory.yaml -g distribution --export health.json

Prerequisites:
    - nornir installed: pip install nornir
    - NAPALM drivers installed: pip install napalm
    - Network devices reachable and credentials configured in inventory
"""

import argparse
import json
import logging
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_device_health(task: Task) -> Result:
    """Collect CPU, memory, and temperature metrics from device."""
    try:
        env_data = task.run(napalm_get, getters=['get_environment'])
        env = env_data[0].result.get('get_environment', {})

        return Result(
            host=task.host,
            result={
                'cpu': env.get('cpu', {}).get('%usage', 0),
                'memory': env.get('memory', {}).get('%usage', 0),
                'temperature': env.get('temperature', {}),
            }
        )
    except Exception as e:
        logger.error(f"{task.host}: Failed to collect health - {e}")
        return Result(host=task.host, failed=True, exception=e)


def evaluate_health_status(cpu: float, memory: float,
                           cpu_warn: float, mem_warn: float) -> tuple:
    """Evaluate device status and return status string and issues."""
    issues = []

    if cpu >= 95 or memory >= 95:
        status = "CRITICAL"
    elif cpu >= cpu_warn or memory >= mem_warn:
        status = "WARNING"
    else:
        status = "OK"

    if cpu >= cpu_warn:
        issues.append(f"CPU {cpu:.1f}%")
    if memory >= mem_warn:
        issues.append(f"Memory {memory:.1f}%")

    return status, issues


def format_health_report(results: dict, format_type: str = 'table') -> str:
    """Format health results for display."""
    if format_type == 'json':
        return json.dumps(results, indent=2)

    report = "\n" + "=" * 80 + "\n"
    report += "DEVICE HEALTH REPORT\n"
    report += "=" * 80 + "\n"
    report += (f"{'Device':<20} {'Status':<12} {'CPU %':<10} "
              f"{'Memory %':<10} {'Issues':<30}\n")
    report += "-" * 80 + "\n"

    for device, data in sorted(results.items()):
        issues_str = ', '.join(data.get('issues', [])) or 'None'
        report += (f"{device:<20} {data['status']:<12} "
                  f"{data['cpu']:>8.1f}% {data['memory']:>8.1f}% "
                  f"{issues_str:<30}\n")

    report += "=" * 80 + "\n"
    return report


def main():
    parser = argparse.ArgumentParser(
        description='Monitor health metrics across network devices'
    )
    parser.add_argument('-i', '--inventory', default='inventory.yaml',
                       help='Path to nornir inventory file')
    parser.add_argument('-d', '--devices',
                       help='Comma-separated device names')
    parser.add_argument('-g', '--group',
                       help='Inventory group to monitor')
    parser.add_argument('--warning-cpu', type=float, default=75,
                       help='CPU usage warning threshold (%)')
    parser.add_argument('--warning-mem', type=float, default=75,
                       help='Memory usage warning threshold (%)')
    parser.add_argument('--format', choices=['table', 'json'],
                       default='table', help='Output format')
    parser.add_argument('--export', help='Export results to JSON file')

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.devices:
            nr = nr.filter(name__in=args.devices.split(','))
        elif args.group:
            nr = nr.filter(group=args.group)
        else:
            logger.error('Specify --devices or --group')
            return 1

        logger.info(f'Collecting health metrics from {len(nr.inventory.hosts)} '
                   'device(s)')
        results = nr.run(task=collect_device_health)

        health_summary = {}
        for host_name, host_results in results.items():
            if host_results[0].failed:
                health_summary[host_name] = {
                    'status': 'UNREACHABLE',
                    'cpu': 0,
                    'memory': 0,
                    'issues': ['Device unreachable']
                }
            else:
                data = host_results[0].result
                status, issues = evaluate_health_status(
                    data['cpu'], data['memory'],
                    args.warning_cpu, args.warning_mem
                )
                health_summary[host_name] = {
                    'status': status,
                    'cpu': data['cpu'],
                    'memory': data['memory'],
                    'issues': issues
                }

        report = format_health_report(health_summary, args.format)
        print(report)

        if args.export:
            with open(args.export, 'w') as f:
                json.dump(health_summary, f, indent=2)
            logger.info(f'Results exported to {args.export}')

        return 0

    except Exception as e:
        logger.error(f'Script execution failed: {e}', exc_info=True)
        return 1


if __name__ == '__main__':
    exit(main())
```