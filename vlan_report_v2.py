```python
"""
Device Fact Collection and Inventory Report Generator

Gathers device facts (hardware, OS, serial numbers, uptime) from network devices
using Nornir and generates formatted inventory reports.

Prerequisites:
  - Nornir installed and configured
  - devices.yaml and groups.yaml in inventory directory
  - Network device access credentials configured
  - netmiko or napalm drivers available for target device types

Usage:
  python device_facts.py --devices all --output json --log-level INFO
  python device_facts.py --devices group:switches --output table
  python device_facts.py --devices device1,device2
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks import networking


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def gather_device_facts(task) -> Dict[str, Any]:
    """Gather facts from a device using NAPALM get_facts."""
    try:
        result = task.run(
            task=networking.napalm_get,
            getters=['facts']
        )
        return result[0].result.get('facts', {})
    except Exception as e:
        logger.error(f"Failed to gather facts from {task.host.name}: {e}")
        return {'error': str(e)}


def generate_inventory_report(facts: Dict[str, Dict], output_format: str) -> None:
    """Generate and display inventory report in specified format."""
    if output_format == 'json':
        print(json.dumps(facts, indent=2))
    elif output_format == 'table':
        print_table_report(facts)


def print_table_report(facts: Dict[str, Dict]) -> None:
    """Print facts in table format."""
    print(f"\n{'Device':<20} {'OS':<15} {'Model':<20} {'Serial':<15} {'Uptime':<10}")
    print("=" * 80)
    
    for device_name, device_facts in facts.items():
        if 'error' in device_facts:
            print(f"{device_name:<20} {'ERROR':<15} {device_facts['error']}")
            continue
        
        os_version = str(device_facts.get('os_version', 'N/A'))[:15]
        model = str(device_facts.get('model', 'N/A'))[:20]
        serial = str(device_facts.get('serial_number', 'N/A'))[:15]
        uptime = format_uptime(device_facts.get('uptime', 0))
        
        print(f"{device_name:<20} {os_version:<15} {model:<20} {serial:<15} {uptime:<10}")


def format_uptime(seconds: int) -> str:
    """Format uptime in seconds to human-readable format."""
    try:
        days = int(seconds) // 86400
        hours = (int(seconds) % 86400) // 3600
        return f"{days}d {hours}h"
    except (TypeError, ValueError):
        return 'N/A'


def filter_devices(inventory, filter_spec: str):
    """Apply filter to device inventory based on filter specification."""
    if not filter_spec or filter_spec == 'all':
        return inventory.hosts
    
    if filter_spec.startswith('group:'):
        group_name = filter_spec.split(':', 1)[1]
        return inventory.filter(F(groups__contains=group_name))
    
    if ',' in filter_spec:
        device_list = [d.strip() for d in filter_spec.split(',')]
        return inventory.filter(F(name__in=device_list))
    
    return inventory.filter(F(name=filter_spec))


def main():
    parser = argparse.ArgumentParser(
        description='Gather and report device facts from network inventory'
    )
    parser.add_argument(
        '--devices',
        default='all',
        help='Target devices: "all", "group:name", or comma-separated list'
    )
    parser.add_argument(
        '--output',
        choices=['json', 'table'],
        default='table',
        help='Output format'
    )
    parser.add_argument(
        '--inventory',
        default='.',
        help='Path to Nornir inventory directory'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level'
    )
    
    args = parser.parse_args()
    logger.setLevel(getattr(logging, args.log_level))
    
    try:
        nr = InitNornir(config_file=Path(args.inventory) / 'config.yaml')
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices")
        
        filtered_hosts = filter_devices(nr.inventory, args.devices)
        logger.info(f"Targeting {len(filtered_hosts)} device(s)")
        
        if not filtered_hosts:
            logger.warning("No devices matched filter criteria")
            return
        
        results = nr.run(task=gather_device_facts, hosts=filtered_hosts)
        
        inventory_facts = {}
        for device_name, task_result in results.items():
            if task_result[0].result:
                inventory_facts[device_name] = task_result[0].result
        
        generate_inventory_report(inventory_facts, args.output)
        logger.info(f"Successfully gathered facts from {len(inventory_facts)} device(s)")
        
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
```