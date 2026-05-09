```python
"""
Device Uptime and Health Report - Network Automation Script

Purpose:
    Collects device uptime, system information, and health metrics
    from network devices to generate a comprehensive health report.

Usage:
    python device_uptime_report.py --inventory inventory/ --device-group routers
    python device_uptime_report.py --inventory inventory/ --device host-rtr-01

Prerequisites:
    - Nornir with napalm plugin
    - Network devices with SSH access configured
    - Inventory YAML files properly configured
"""

import argparse
import logging
from typing import Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def calculate_uptime_days(uptime_seconds: int) -> float:
    """Convert uptime in seconds to days."""
    return uptime_seconds / 86400


def assess_health(uptime_days: float, cpu_percent: float) -> str:
    """
    Assess device health based on uptime and CPU usage.
    
    Args:
        uptime_days: Device uptime in days
        cpu_percent: CPU utilization percentage
        
    Returns:
        Health status string
    """
    if uptime_days < 1:
        return "CRITICAL - Recently rebooted"
    if cpu_percent > 80:
        return "WARNING - High CPU utilization"
    if uptime_days < 30:
        return "CAUTION - Recent reboot"
    return "HEALTHY"


def collect_device_health(nr, device_filter=None):
    """
    Collect health metrics from devices.
    
    Args:
        nr: Nornir inventory
        device_filter: Optional device name or group filter
        
    Returns:
        Dictionary of device health data
    """
    if device_filter:
        filtered = nr.filter(name=device_filter)
        if not filtered.inventory.hosts:
            filtered = nr.filter(F(groups__contains=device_filter))
    else:
        filtered = nr
    
    logger.info(f"Collecting health data from {len(filtered.inventory.hosts)} device(s)")
    
    results = filtered.run(
        task=napalm_get,
        getters=['get_facts', 'get_environment']
    )
    
    health_data = {}
    
    for device_name, task_result in results.items():
        if task_result.failed:
            logger.warning(f"{device_name}: Failed to retrieve health data")
            health_data[device_name] = {'status': 'FAILED'}
            continue
        
        try:
            facts = task_result[0].result.get('get_facts', {})
            env = task_result[0].result.get('get_environment', {})
            
            uptime_sec = facts.get('uptime', 0)
            uptime_days = calculate_uptime_days(uptime_sec)
            
            cpu_percent = 0.0
            if env.get('cpu') and isinstance(env['cpu'], list) and env['cpu']:
                cpu_percent = env['cpu'][0].get('%usage', 0.0)
            
            health_status = assess_health(uptime_days, cpu_percent)
            
            health_data[device_name] = {
                'hostname': facts.get('hostname', 'N/A'),
                'os': facts.get('os_version', 'N/A'),
                'model': facts.get('model', 'N/A'),
                'serial': facts.get('serial_number', 'N/A'),
                'uptime_days': round(uptime_days, 2),
                'cpu_percent': cpu_percent,
                'status': health_status,
                'vendor': facts.get('vendor', 'N/A')
            }
            
            logger.info(f"{device_name}: {health_status}")
            
        except (KeyError, TypeError, IndexError) as e:
            logger.error(f"{device_name}: Failed to parse health data - {e}")
            health_data[device_name] = {'status': 'PARSE_ERROR'}
    
    return health_data


def print_health_report(health_data: Dict):
    """
    Print formatted health report.
    
    Args:
        health_data: Dictionary of health metrics by device
    """
    if not health_data:
        print("No health data collected")
        return
    
    print("\n" + "=" * 90)
    print("DEVICE HEALTH REPORT")
    print("=" * 90)
    print(f"{'Device':<20} {'Status':<30} {'Uptime (Days)':<15} {'CPU %':<10}")
    print("-" * 90)
    
    for device, data in sorted(health_data.items()):
        status = data.get('status', 'UNKNOWN')
        uptime = data.get('uptime_days', 'N/A')
        cpu = data.get('cpu_percent', 'N/A')
        
        if isinstance(uptime, float):
            uptime_str = f"{uptime:.2f}"
        else:
            uptime_str = str(uptime)
        
        if isinstance(cpu, float):
            cpu_str = f"{cpu:.1f}"
        else:
            cpu_str = str(cpu)
        
        print(f"{device:<20} {status:<30} {uptime_str:<15} {cpu_str:<10}")
    
    print("-" * 90)
    
    if health_data:
        print("\nDetailed Information:")
        for device, data in sorted(health_data.items()):
            if data.get('status') in ['FAILED', 'PARSE_ERROR']:
                continue
            
            print(f"\n{device}:")
            print(f"  Hostname: {data.get('hostname', 'N/A')}")
            print(f"  Vendor: {data.get('vendor', 'N/A')}")
            print(f"  Model: {data.get('model', 'N/A')}")
            print(f"  OS Version: {data.get('os', 'N/A')}")
            print(f"  Serial: {data.get('serial', 'N/A')}")
    
    print("\n" + "=" * 90)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Generate device uptime and health report'
    )
    parser.add_argument(
        '--inventory',
        required=True,
        help='Path to Nornir inventory directory'
    )
    parser.add_argument(
        '--device',
        help='Analyze specific device by name'
    )
    parser.add_argument(
        '--device-group',
        help='Analyze devices in specific group'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")
        
        device_filter = args.device or args.device_group
        health_data = collect_device_health(nr, device_filter)
        print_health_report(health_data)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```