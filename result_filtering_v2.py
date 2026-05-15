```python
"""
Device Health Check via Nornir and NAPALM.

Collects operational metrics (uptime, CPU, memory, interface errors) from network
devices and generates a health summary report. Useful for monitoring device state
and identifying interface anomalies.

Usage:
    python device_health_check.py -i inventory.yaml -g switches
    python device_health_check.py -d router01 --format json

Prerequisites:
    - Nornir installed (pip install nornir nornir-napalm)
    - NAPALM drivers for target device types
    - Inventory file with device credentials
"""

import logging
import argparse
import json
from typing import Dict, List, Optional, Any
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_health_metrics(task) -> Optional[Dict[str, Any]]:
    """Retrieve device health metrics: uptime, CPU, memory, temperature."""
    try:
        result = task.run(napalm_get, getters=['facts', 'environment'])
        facts = result[0].result.get('facts', {})
        environment = result[0].result.get('environment', {})

        cpu_util = environment.get('cpu', {}).get('0', {}).get('%usage', 0)
        mem_util = environment.get('memory', {}).get('%usage', 0)

        return {
            'device': task.host.name,
            'uptime_seconds': facts.get('uptime_seconds', 0),
            'cpu_utilization': cpu_util,
            'memory_utilization': mem_util,
            'vendor': facts.get('vendor', 'Unknown'),
        }
    except Exception as e:
        logger.error(f"Failed to gather metrics from {task.host.name}: {e}")
        return None


def gather_interface_health(task) -> Optional[Dict[str, Any]]:
    """Retrieve interface error statistics across all interfaces."""
    try:
        result = task.run(napalm_get, getters=['interfaces'])
        interfaces = result[0].result.get('interfaces', {})

        unhealthy_interfaces = {}
        for iface_name, iface_data in interfaces.items():
            rx_err = iface_data.get('rx_errors', 0)
            tx_err = iface_data.get('tx_errors', 0)
            if rx_err > 0 or tx_err > 0:
                unhealthy_interfaces[iface_name] = {
                    'rx_errors': rx_err,
                    'tx_errors': tx_err,
                    'rx_discards': iface_data.get('rx_discards', 0),
                    'tx_discards': iface_data.get('tx_discards', 0),
                }

        return {
            'device': task.host.name,
            'error_count': len(unhealthy_interfaces),
            'interfaces': unhealthy_interfaces,
        }
    except Exception as e:
        logger.error(f"Failed to gather interface stats from {task.host.name}: {e}")
        return None


def format_uptime(seconds: int) -> str:
    """Convert seconds to readable uptime string."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def print_table_report(health: List[Dict], interfaces: List[Dict]) -> None:
    """Print health metrics in table format."""
    print("\n" + "=" * 80)
    print(f"{'Device':<18} {'Uptime':<18} {'CPU %':<10} {'Memory %':<12} {'Status':<20}")
    print("=" * 80)

    for item in health:
        if not item:
            continue
        uptime = format_uptime(item['uptime_seconds'])
        cpu = item['cpu_utilization']
        mem = item['memory_utilization']
        status = "Healthy" if cpu < 80 and mem < 80 else "Warning"
        print(f"{item['device']:<18} {uptime:<18} {cpu:<10.1f} {mem:<12.1f} {status:<20}")

    print("\n" + "=" * 80)
    print("Interface Error Summary:")
    print("=" * 80)
    for item in interfaces:
        if not item or item['error_count'] == 0:
            continue
        print(f"\n{item['device']} ({item['error_count']} interfaces with errors):")
        for iface, stats in item['interfaces'].items():
            print(f"  {iface:<15} RX_ERR={stats['rx_errors']:<5} "
                  f"TX_ERR={stats['tx_errors']:<5} "
                  f"RX_DISC={stats['rx_discards']:<5} TX_DISC={stats['tx_discards']:<5}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Collect and report device health metrics'
    )
    parser.add_argument(
        '-i', '--inventory',
        default='inventory.yaml',
        help='Path to Nornir inventory file'
    )
    parser.add_argument(
        '-g', '--group',
        help='Filter devices by group name'
    )
    parser.add_argument(
        '-d', '--device',
        help='Filter by specific device name'
    )
    parser.add_argument(
        '-f', '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format'
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
        if args.device:
            nr = nr.filter(F(name=args.device))

        if len(nr.inventory.hosts) == 0:
            logger.error("No devices matched filter criteria")
            return 1

        logger.info(f"Gathering health metrics from {len(nr.inventory.hosts)} device(s)")
        health_results = nr.run(task=gather_health_metrics)
        interface_results = nr.run(task=gather_interface_health)

        health_data = [
            health_results[host][0].result
            for host in health_results
            if health_results[host][0].result
        ]
        interface_data = [
            interface_results[host][0].result
            for host in interface_results
            if interface_results[host][0].result
        ]

        if args.format == 'json':
            output = {
                'health_metrics': health_data,
                'interface_errors': interface_data,
            }
            print(json.dumps(output, indent=2))
        else:
            print_table_report(health_data, interface_data)

        logger.info("Health check completed successfully")
        return 0

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```