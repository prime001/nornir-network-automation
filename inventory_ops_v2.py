```python
"""
Device Inventory Validation Script

Validates network device inventory data by gathering actual device facts
and comparing against the inventory database. Identifies discrepancies
in device model, OS version, serial numbers, and other critical attributes.

Prerequisites:
- Nornir installed and configured with valid inventory
- Devices accessible via SSH with credentials in inventory
- NAPALM driver installed for target device types

Usage:
    python 019_device_inventory_validation.py
    python 019_device_inventory_validation.py --group leaf
    python 019_device_inventory_validation.py --output json
    python 019_device_inventory_validation.py --devices device1 device2

Examples:
    # Validate all devices
    python 019_device_inventory_validation.py

    # Validate only leaf devices
    python 019_device_inventory_validation.py --group leaf

    # Export results as JSON
    python 019_device_inventory_validation.py --output json > results.json
"""

import argparse
import json
import logging
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(debug: bool = False) -> None:
    """Configure logging with appropriate level."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def gather_device_facts(task) -> Dict[str, Any]:
    """Gather actual device facts using NAPALM and validate against inventory."""
    try:
        result = task.run(task=napalm_get, getters=['facts'])
        facts = result[0].result['facts']
        
        actual_facts = {
            'model': facts.get('model'),
            'os_version': facts.get('os_version'),
            'serial_number': facts.get('serial_number'),
            'vendor': facts.get('vendor'),
            'uptime': facts.get('uptime'),
            'hostname': facts.get('hostname')
        }
        
        discrepancies = {}
        
        if task.host.get('model') and task.host['model'] != actual_facts['model']:
            discrepancies['model'] = {
                'expected': task.host['model'],
                'actual': actual_facts['model']
            }
        
        if task.host.get('os_version') and task.host['os_version'] != actual_facts['os_version']:
            discrepancies['os_version'] = {
                'expected': task.host['os_version'],
                'actual': actual_facts['os_version']
            }
        
        if task.host.get('serial_number') and task.host['serial_number'] != actual_facts['serial_number']:
            discrepancies['serial_number'] = {
                'expected': task.host['serial_number'],
                'actual': actual_facts['serial_number']
            }
        
        return {
            'device': task.host.name,
            'valid': len(discrepancies) == 0,
            'actual_facts': actual_facts,
            'discrepancies': discrepancies,
            'error': None
        }
        
    except Exception as e:
        logging.error(f"Error gathering facts from {task.host.name}: {e}")
        return {
            'device': task.host.name,
            'valid': False,
            'actual_facts': None,
            'discrepancies': {},
            'error': str(e)
        }


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description='Validate network device inventory data against actual device facts'
    )
    
    parser.add_argument(
        '--group',
        type=str,
        help='Filter by inventory group'
    )
    parser.add_argument(
        '--role',
        type=str,
        help='Filter by device role'
    )
    parser.add_argument(
        '--devices',
        nargs='+',
        help='Specific devices to validate'
    )
    parser.add_argument(
        '--output',
        choices=['text', 'json'],
        default='text',
        help='Output format'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    setup_logging(args.debug)
    
    try:
        nr = InitNornir(config_file='config.yaml')
    except Exception as e:
        logging.error(f"Failed to initialize Nornir: {e}")
        return
    
    if args.devices:
        nr = nr.filter(F(name__in=args.devices))
    if args.group:
        nr = nr.filter(F(groups__contains=args.group))
    if args.role:
        nr = nr.filter(F(data__role=args.role))
    
    if len(nr.inventory.hosts) == 0:
        logging.warning("No devices matched the filter criteria")
        return
    
    logging.info(f"Validating {len(nr.inventory.hosts)} devices")
    
    results = nr.run(task=gather_device_facts)
    
    validation_results = {}
    for host_name, task_results in results.items():
        host_result = task_results[0].result
        validation_results[host_name] = host_result
    
    if args.output == 'json':
        print(json.dumps(validation_results, indent=2, default=str))
    else:
        valid_count = sum(1 for r in validation_results.values() if r['valid'])
        invalid_count = len(validation_results) - valid_count
        
        print(f"\n{'='*60}")
        print(f"Inventory Validation Results: {valid_count} valid, {invalid_count} with discrepancies")
        print(f"{'='*60}\n")
        
        for device, data in validation_results.items():
            status = "✓ VALID" if data['valid'] else "✗ INVALID"
            print(f"{device:30} {status}")
            
            if not data['valid']:
                if data['error']:
                    print(f"  Error: {data['error']}")
                else:
                    for field, diff in data['discrepancies'].items():
                        print(f"  {field}:")
                        print(f"    Expected: {diff['expected']}")
                        print(f"    Actual:   {diff['actual']}")


if __name__ == "__main__":
    main()
```