```python
"""
Device Health Monitor - Nornir-based network device health checker.

Purpose:
    Monitors health metrics on network devices including uptime, interface
    status, and system information. Generates a summary health report.

Usage:
    python device_health_monitor.py --inventory hosts.yaml --username admin \
        --password secret --timeout 30

Prerequisites:
    - nornir with NAPALM plugin
    - Network devices with SSH access
    - Inventory file (YAML format)
    - Valid device credentials
"""

import logging
import argparse
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logger = logging.getLogger(__name__)


def get_device_health(task: Task) -> Result:
    """Retrieve device health metrics using NAPALM."""
    try:
        facts_result = task.run(napalm_get, getters=['facts'])
        interfaces_result = task.run(napalm_get, getters=['interfaces'])
        
        facts = facts_result[0].result['facts']
        interfaces = interfaces_result[0].result.get('interfaces', {})
        
        up_interfaces = sum(1 for iface in interfaces.values() 
                           if iface.get('is_up', False))
        
        health_data = {
            'hostname': task.host.name,
            'uptime': facts.get('uptime'),
            'os_version': facts.get('os_version'),
            'interfaces_up': up_interfaces,
            'interfaces_total': len(interfaces),
        }
        
        return Result(host=task.host, result=health_data)
        
    except Exception as e:
        logger.error(f"Health check failed for {task.host.name}: {e}")
        return Result(host=task.host, result=None, failed=True, exception=e)


def print_report(results):
    """Print formatted health report."""
    print("\n" + "="*70)
    print("DEVICE HEALTH REPORT")
    print("="*70 + "\n")
    
    healthy, warnings, errors = 0, 0, 0
    
    for device_name, task_results in results.items():
        result = task_results[0]
        
        if result.failed:
            print(f"❌ {device_name}: ERROR - {result.exception}")
            errors += 1
            continue
        
        data = result.result
        uptime_hours = data['uptime'] // 3600 if data['uptime'] else 0
        
        print(f"✓ {device_name}")
        print(f"  OS: {data['os_version']} | Uptime: {uptime_hours}h")
        print(f"  Interfaces: {data['interfaces_up']}/{data['interfaces_total']} up\n")
        
        if uptime_hours < 1:
            print(f"  ⚠ WARNING: Device recently rebooted\n")
            warnings += 1
        else:
            healthy += 1
    
    print("="*70)
    print(f"Summary: {healthy} healthy, {warnings} warnings, {errors} errors")
    print("="*70 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Monitor network device health'
    )
    parser.add_argument('--inventory', required=True, 
                       help='Nornir inventory file (YAML)')
    parser.add_argument('--username', required=True, help='Device username')
    parser.add_argument('--password', required=True, help='Device password')
    parser.add_argument('--timeout', type=int, default=30,
                       help='Connection timeout (default: 30s)')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    logger.info("Initializing Nornir from inventory...")
    try:
        nr = InitNornir(
            inventory={
                'plugin': 'nornir.plugins.inventory.yaml.YAMLInventory',
                'options': {'host_file': args.inventory}
            }
        )
    except Exception as e:
        logger.error(f"Failed to load inventory: {e}")
        return 1
    
    for host in nr.inventory.hosts.values():
        host.username = args.username
        host.password = args.password
    
    logger.info(f"Running health checks on {len(nr.inventory.hosts)} devices...")
    
    try:
        results = nr.run(task=get_device_health)
        print_report(results)
    except Exception as e:
        logger.error(f"Task execution failed: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main())
```