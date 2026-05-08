```python
"""
Device System Health Monitor

Purpose:
  Collects and monitors system metrics (uptime, CPU, memory, temperature) from
  network devices using Nornir and NAPALM. Generates health reports, identifies
  at-risk devices, and flags metric thresholds.

Usage:
  python device_health_monitor.py --devices all --threshold-cpu 80
  python device_health_monitor.py --devices-group core --format json
  python device_health_monitor.py --devices router01,router02 --alert

Prerequisites:
  - Nornir with NAPALM support installed
  - Network devices with SNMP enabled and accessible
  - nornir_inventory.yaml with device credentials configured
  - Device support for get_facts and get_environment NAPALM methods
"""

import argparse
import json
import logging
from typing import Dict, List, Any
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_health_metrics(task: Task) -> Result:
    """Collect system facts and environmental metrics from device."""
    health_data = {
        'device': task.host.name,
        'reachable': False,
        'facts': {},
        'environment': {},
        'metrics': {},
        'alerts': []
    }

    try:
        result = task.run(
            napalm_get,
            getters=['facts', 'environment']
        )

        if result[0].result:
            data = result[0].result
            health_data['facts'] = data.get('facts', {})
            health_data['environment'] = data.get('environment', {})
            health_data['reachable'] = True

            health_data['metrics'] = {
                'uptime_seconds': health_data['facts'].get('uptime_seconds', 0),
                'os_version': health_data['facts'].get('os_version', 'Unknown'),
                'model': health_data['facts'].get('model', 'Unknown'),
                'serial_number': health_data['facts'].get('serial_number', 'Unknown')
            }

    except Exception as e:
        health_data['alerts'].append(f"Collection failed: {str(e)}")
        logger.warning(f"Failed to collect health data from {task.host.name}: {e}")

    return Result(host=task.host, result=health_data)


def validate_thresholds(health_data: Dict, cpu_threshold: int,
                       temp_threshold: int) -> None:
    """Check metrics against configured thresholds."""
    env = health_data.get('environment', {})

    if 'cpu' in env:
        for cpu_name, cpu_info in env['cpu'].items():
            if isinstance(cpu_info, dict):
                usage = cpu_info.get('%Usage', cpu_info.get('%usage', 0))
                if usage and usage > cpu_threshold:
                    health_data['alerts'].append(
                        f"CPU {cpu_name}: {usage}% (threshold: {cpu_threshold}%)"
                    )

    if 'temperature' in env:
        for temp_name, temp_info in env['temperature'].items():
            if isinstance(temp_info, dict):
                current = temp_info.get('current_reading')
                if current and current > temp_threshold:
                    health_data['alerts'].append(
                        f"Temperature {temp_name}: {current}C (threshold: {temp_threshold}C)"
                    )

    if 'power' in env:
        for psu_name, psu_info in env['power'].items():
            if isinstance(psu_info, dict) and not psu_info.get('status', True):
                health_data['alerts'].append(f"Power supply {psu_name}: FAILED")


def format_results(results: Dict[str, Any], output_format: str) -> str:
    """Format results for output."""
    if output_format == 'json':
        return json.dumps(results, indent=2, default=str)

    lines = []
    lines.append(f"{'Device':<15} {'Reachable':<12} {'Uptime':<20} {'Alerts':<40}")
    lines.append("-" * 87)

    for device, data in results.items():
        reachable = 'Yes' if data['reachable'] else 'No'
        uptime = _format_uptime(data['metrics'].get('uptime_seconds', 0))
        alerts = '; '.join(data['alerts'][:2]) if data['alerts'] else 'None'

        lines.append(
            f"{device:<15} {reachable:<12} {uptime:<20} {alerts:<40}"
        )

    return '\n'.join(lines)


def _format_uptime(seconds: int) -> str:
    """Convert seconds to human-readable uptime format."""
    if not seconds:
        return 'N/A'
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h"


def main():
    parser = argparse.ArgumentParser(
        description='Monitor network device health and system metrics'
    )
    parser.add_argument(
        '--devices',
        type=str,
        default='all',
        help='Target devices (comma-separated) or "all"'
    )
    parser.add_argument(
        '--devices-group',
        type=str,
        help='Filter by group from inventory'
    )
    parser.add_argument(
        '--threshold-cpu',
        type=int,
        default=85,
        help='CPU usage alert threshold (percent)'
    )
    parser.add_argument(
        '--threshold-temp',
        type=int,
        default=75,
        help='Temperature alert threshold (Celsius)'
    )
    parser.add_argument(
        '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format'
    )
    parser.add_argument(
        '--alert',
        action='store_true',
        help='Show only devices with alerts'
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file='nornir_inventory.yaml')

        if args.devices_group:
            nr = nr.filter(group=args.devices_group)
        elif args.devices != 'all':
            target_devices = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(lambda h: h.name in target_devices)

        logger.info(f"Collecting health metrics from {len(nr.inventory.hosts)} device(s)")

        results_obj = nr.run(task=collect_health_metrics)

        health_report = {}
        for host, task_results in results_obj.items():
            host_data = task_results[0].result
            validate_thresholds(host_data, args.threshold_cpu, args.threshold_temp)

            if args.alert and not host_data['alerts']:
                continue

            health_report[host] = host_data

        output = format_results(health_report, args.format)
        print(output)

        reachable = sum(1 for h in health_report.values() if h['reachable'])
        with_alerts = sum(1 for h in health_report.values() if h['alerts'])
        logger.info(
            f"Summary: {reachable}/{len(health_report)} reachable, "
            f"{with_alerts} with alerts"
        )

        return 0

    except FileNotFoundError:
        logger.error("Configuration file 'nornir_inventory.yaml' not found")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == '__main__':
    exit(main())
```