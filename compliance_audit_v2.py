```python
"""
Collect and report device facts and configuration summary from network devices.

This script connects to network devices and gathers hardware/software information,
including model, serial number, OS version, and uptime. Results can be filtered by
device or group and exported to JSON for compliance and asset tracking.

Prerequisites:
  - Nornir inventory configured with devices
  - Devices must be reachable via SSH/API
  - Connection type must be supported (Netmiko, NAPALM, or native)
  - credentials configured in Nornir defaults or per-host

Usage:
  python device_facts_collector.py
  python device_facts_collector.py --devices router1 router2
  python device_facts_collector.py --group core --output facts.json
  python device_facts_collector.py --filter platform=ios
"""

import argparse
import json
import logging
import sys
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_device_facts(task: Task) -> Result:
    """Collect device facts using available methods."""
    device = task.host
    facts = {
        'hostname': device.name,
        'ip': device.host,
        'platform': device.get('platform', 'unknown'),
        'os': device.get('os', ''),
        'model': device.get('model', ''),
        'serial_number': '',
        'uptime': '',
        'facts': {},
        'status': 'pending',
        'errors': []
    }
    
    try:
        try:
            from nornir_napalm.plugins.tasks import napalm_get
            result = task.run(task=napalm_get, getters=['facts', 'interfaces'])
            
            if result and result.ok:
                fact_data = result.result.get('facts', {})
                facts['os'] = fact_data.get('os_version', '')
                facts['model'] = fact_data.get('model', '')
                facts['serial_number'] = fact_data.get('serial_number', '')
                facts['uptime'] = fact_data.get('uptime', '')
                facts['facts'] = fact_data
                facts['status'] = 'success'
                logger.info(f"✓ {device.name}: Facts collected via NAPALM")
            else:
                facts['status'] = 'partial'
                logger.warning(f"⚠ {device.name}: Incomplete NAPALM response")
                
        except ImportError:
            try:
                from nornir_netmiko.tasks import netmiko_send_command
                
                cmd_result = task.run(
                    task=netmiko_send_command,
                    command_string='show version | include Model|Serial'
                )
                
                if cmd_result and cmd_result.ok:
                    facts['status'] = 'success'
                    facts['facts']['raw_output'] = cmd_result.result
                    logger.info(f"✓ {device.name}: Facts collected via Netmiko")
                else:
                    raise Exception("Netmiko command failed")
                    
            except Exception as e:
                facts['status'] = 'failed'
                facts['errors'].append(f"Netmiko failed: {str(e)}")
                facts['facts']['fallback'] = device.data
                logger.warning(f"⚠ {device.name}: Using inventory fallback - {str(e)}")
    
    except Exception as e:
        facts['status'] = 'error'
        facts['errors'].append(str(e))
        logger.error(f"✗ {device.name}: {str(e)}")
    
    return Result(host=device, result=facts)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--devices',
        nargs='+',
        help='Specific device names to target (space-separated)'
    )
    parser.add_argument(
        '--group',
        help='Filter devices by group name'
    )
    parser.add_argument(
        '--filter',
        help='Custom filter expression (e.g., "platform=ios")'
    )
    parser.add_argument(
        '--output',
        help='Output file for JSON results'
    )
    parser.add_argument(
        '--table',
        action='store_true',
        help='Display results as formatted table'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir()
        logger.info(f"Loaded {len(nr.inventory.hosts)} hosts from inventory")
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        logger.error("Ensure config.yaml exists or NORNIR_* env vars are set")
        return 1
    
    if args.devices:
        nr = nr.filter(F(name__in=args.devices))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))
    
    if not nr.inventory.hosts:
        logger.error("No devices matched filter criteria")
        return 1
    
    logger.info(f"Collecting facts from {len(nr.inventory.hosts)} device(s)...")
    
    try:
        results = nr.run(task=collect_device_facts)
        
        facts_output = {}
        success_count = 0
        error_count = 0
        
        for device_name, multi_result in results.items():
            fact_data = multi_result[0].result
            facts_output[device_name] = fact_data
            
            if fact_data['status'] in ['success', 'partial']:
                success_count += 1
            else:
                error_count += 1
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(facts_output, f, indent=2, default=str)
            logger.info(f"Results written to {args.output}")
        
        if args.table or not args.output:
            print("\n" + "=" * 80)
            print(f"{'Device':<20} {'IP Address':<18} {'Platform':<12} {'Model':<20} {'Status':<10}")
            print("=" * 80)
            
            for device_name, fact_data in sorted(facts_output.items()):
                status_icon = "✓" if fact_data['status'] == 'success' else "⚠"
                print(
                    f"{device_name:<20} {fact_data['ip']:<18} "
                    f"{fact_data['platform']:<12} {fact_data['model']:<20} "
                    f"{status_icon} {fact_data['status']:<8}"
                )
            print("=" * 80)
        
        logger.info(
            f"Collection complete: {success_count} successful, {error_count} errors"
        )
        
        return 0 if error_count == 0 else 1
    
    except Exception as e:
        logger.error(f"Task execution failed: {e}", exc_info=args.verbose)
        return 2


if __name__ == '__main__':
    sys.exit(main())
```