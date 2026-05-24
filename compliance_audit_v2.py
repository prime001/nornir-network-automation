```python
"""
Device Health Check Script

Purpose:
    Comprehensive health check for network devices using Nornir.
    Validates device reachability, uptime, CPU/memory utilization,
    software version, and device model across the inventory.

Usage:
    python device_health_check.py --verbose --filter "location=NYC"
    python device_health_check.py --group core-routers --report

Prerequisites:
    - Nornir installed: pip install nornir
    - Inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
    - Devices reachable via SSH
    - NAPALM driver configured for device types
"""

import logging
import argparse
from datetime import datetime
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_device_health(task):
    """Retrieve health metrics from a device using NAPALM."""
    try:
        result = task.run(
            napalm_get,
            getters=['facts', 'get_environment']
        )
        return result
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to retrieve health data - {e}")
        return None


def parse_health_data(host, result):
    """Extract and parse health metrics from NAPALM results."""
    if not result or result.failed:
        return {
            'host': host.name,
            'status': 'FAILED',
            'uptime_days': 'N/A',
            'cpu_percent': 'N/A',
            'memory_percent': 'N/A',
            'version': 'N/A'
        }

    try:
        facts = result[0].result.get('facts', {})
        environment = result[0].result.get('get_environment', {})
        
        uptime_seconds = facts.get('uptime', 0)
        uptime_days = uptime_seconds // 86400 if uptime_seconds else 0
        
        cpu_percent = 'N/A'
        memory_percent = 'N/A'
        
        if environment.get('cpu'):
            cpu_dict = environment['cpu']
            if isinstance(cpu_dict, dict) and cpu_dict:
                first_cpu = list(cpu_dict.values())[0]
                cpu_percent = first_cpu.get('%usage', 'N/A')
        
        if environment.get('memory'):
            mem_dict = environment['memory']
            total_ram = mem_dict.get('available_ram', 1)
            used_ram = mem_dict.get('used_ram', 0)
            if total_ram:
                memory_percent = round((used_ram / total_ram * 100), 2)
        
        return {
            'host': host.name,
            'status': 'OK',
            'uptime_days': uptime_days,
            'cpu_percent': cpu_percent,
            'memory_percent': memory_percent,
            'version': facts.get('os_version', 'N/A'),
            'model': facts.get('model', 'N/A')
        }
    except Exception as e:
        logger.error(f"Error parsing health data for {host.name}: {e}")
        return {'host': host.name, 'status': 'PARSE_ERROR'}


def print_health_report(health_data):
    """Format and display health check results."""
    print("\n" + "=" * 110)
    print(f"{'Hostname':<20} {'Status':<12} {'Uptime(d)':<12} "
          f"{'CPU(%)':<10} {'Memory(%)':<12} {'Model':<20}")
    print("=" * 110)
    
    for data in health_data:
        uptime = str(data.get('uptime_days', 'N/A'))
        cpu = str(data.get('cpu_percent', 'N/A'))
        memory = str(data.get('memory_percent', 'N/A'))
        model = str(data.get('model', 'N/A'))[:19]
        
        print(f"{data['host']:<20} {data['status']:<12} {uptime:<12} "
              f"{cpu:<10} {memory:<12} {model:<20}")
    
    print("=" * 110 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Perform health checks on network devices'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--group',
        type=str,
        help='Filter by device group'
    )
    parser.add_argument(
        '--filter',
        type=str,
        help='Apply custom filter (e.g., "location=NYC")'
    )
    parser.add_argument(
        '--report',
        action='store_true',
        help='Save detailed health report to file'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file="config.yaml")
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices from inventory")
        
        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
            logger.info(f"Filtered to group '{args.group}': "
                       f"{len(nr.inventory.hosts)} devices")
        
        if args.filter:
            key, value = args.filter.split('=')
            nr = nr.filter(F(**{f"data__{key}": value}))
            logger.info(f"Applied custom filter: "
                       f"{len(nr.inventory.hosts)} devices remaining")
        
        if not nr.inventory.hosts:
            logger.warning("No devices matched filter criteria")
            return
        
        logger.info("Starting health checks...")
        results = nr.run(task=get_device_health)
        logger.info("Health checks completed")
        
        health_data = []
        for host_name in results.keys():
            host = nr.inventory.hosts[host_name]
            health = parse_health_data(host, results[host_name])
            health_data.append(health)
        
        print_health_report(health_data)
        
        if args.report:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = f"health_report_{timestamp}.txt"
            with open(report_file, 'w') as f:
                f.write(f"Device Health Report\n")
                f.write(f"Generated: {datetime.now()}\n\n")
                for data in health_data:
                    f.write(f"{data['host']}:\n")
                    for key, value in data.items():
                        if key != 'host':
                            f.write(f"  {key}: {value}\n")
                    f.write("\n")
            logger.info(f"Report saved to {report_file}")
        
        failed = sum(1 for h in health_data if h['status'] != 'OK')
        if failed:
            logger.warning(f"{failed} device(s) failed health check")
        else:
            logger.info("All devices passed health check")
    
    except Exception as e:
        logger.error(f"Critical error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```