```python
"""
Device Health Monitor

Purpose:
    Collect and report system health metrics (CPU, memory, uptime, temperature)
    from network devices using Nornir and NAPALM.

Usage:
    python device_health_monitor.py --inventory inventory.yaml \
        --device-filter sw01 --output json

Prerequisites:
    - Nornir installed (pip install nornir)
    - Device inventory in YAML format with NAPALM driver support
    - Network connectivity to devices
    - Devices must support NAPALM get_facts and get_environment
"""

import argparse
import json
import logging
import sys
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_health_metrics(task) -> Dict[str, Any]:
    """Collect device health metrics using NAPALM getters."""
    health = {}

    try:
        facts_result = task.run(
            task=napalm_get,
            getters=['facts'],
            severity_level=logging.WARNING
        )
        if facts_result:
            facts = facts_result.result.get('facts', {})
            health['uptime_seconds'] = facts.get('uptime', 0)
            health['os_version'] = facts.get('os_version', 'Unknown')
            health['serial_number'] = facts.get('serial_number', 'Unknown')

        env_result = task.run(
            task=napalm_get,
            getters=['environment'],
            severity_level=logging.WARNING
        )
        if env_result:
            env = env_result.result.get('environment', {})

            if env.get('cpu'):
                health['cpu_utilization'] = {
                    k: v.get('%usage', 0)
                    for k, v in env['cpu'].items()
                }

            if env.get('memory'):
                mem = env['memory']
                health['memory_mb'] = {
                    'used': mem.get('used_ram', 0),
                    'available': mem.get('available_ram', 0)
                }

            if env.get('temperature'):
                health['temperature_celsius'] = {
                    k: v.get('current_temperature', 0)
                    for k, v in env['temperature'].items()
                }

    except Exception as e:
        logger.error(f"Error collecting metrics: {e}")
        health['error'] = str(e)

    return health


def format_text_output(results: Dict) -> str:
    """Format results as human-readable text."""
    lines = ["\n" + "=" * 70, "DEVICE HEALTH REPORT", "=" * 70]

    for device_name, task_result in results.items():
        lines.append(f"\n{device_name}:")
        lines.append("-" * 50)

        if task_result[0].failed:
            lines.append(f"  Status: FAILED - {task_result[0].exception}")
            continue

        health = task_result[0].result
        if 'error' in health:
            lines.append(f"  Status: ERROR - {health['error']}")
            continue

        lines.append(f"  OS Version: {health.get('os_version', 'N/A')}")
        lines.append(f"  Uptime (seconds): {health.get('uptime_seconds', 'N/A')}")

        if 'cpu_utilization' in health:
            lines.append("  CPU Utilization:")
            for cpu_id, usage in health['cpu_utilization'].items():
                lines.append(f"    {cpu_id}: {usage}%")

        if 'memory_mb' in health:
            mem = health['memory_mb']
            lines.append(f"  Memory: {mem.get('used', 0)}MB used / "
                        f"{mem.get('available', 0)}MB available")

        if 'temperature_celsius' in health:
            lines.append("  Temperature:")
            for sensor, temp in health['temperature_celsius'].items():
                lines.append(f"    {sensor}: {temp}°C")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Collect device health metrics from network devices'
    )
    parser.add_argument('--inventory', required=True,
                        help='Path to Nornir inventory file')
    parser.add_argument('--device-filter',
                        help='Filter devices by name substring')
    parser.add_argument('--output', choices=['text', 'json'],
                        default='text', help='Output format')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.device_filter:
            nr = nr.filter(F(name__contains=args.device_filter))

        if not nr.inventory.hosts:
            logger.error("No devices found")
            sys.exit(1)

        logger.info(f"Collecting health metrics from {len(nr.inventory.hosts)} "
                   f"device(s)")

        results = nr.run(task=collect_health_metrics)

        if args.output == 'json':
            output = {}
            for device, task_result in results.items():
                output[device] = (task_result[0].result if not task_result[0].failed
                                 else {'error': str(task_result[0].exception)})
            print(json.dumps(output, indent=2))
        else:
            print(format_text_output(results))

        sys.exit(0 if not results.failed else 1)

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```