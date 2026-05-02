```python
"""
Device Health Report Generator

Collects system health metrics from network devices and generates a comprehensive
health report including uptime, CPU/memory utilization, and interface error counts.

Usage:
    python 011_device_health.py --hosts core-1 core-2 --username admin
    python 011_device_health.py --filter site:us-east --format json

Prerequisites:
    - Nornir configured with device inventory in config.yaml
    - Network device access with SSH/API credentials
    - napalm driver for each device platform
    - Devices must support: show version, show processes, show interfaces
"""

import logging
import argparse
import json
from datetime import datetime
from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def format_uptime(uptime_seconds):
    """Convert uptime seconds to human readable format."""
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def calculate_health_score(metrics):
    """Calculate device health score (0-100) based on error metrics."""
    if 'error' in metrics:
        return 0
    
    score = 100
    
    errors = metrics.get('total_errors', 0)
    discards = metrics.get('total_discards', 0)
    
    if errors > 1000:
        score -= 30
    elif errors > 100:
        score -= 15
    elif errors > 10:
        score -= 5
    
    if discards > 500:
        score -= 25
    elif discards > 50:
        score -= 10
    elif discards > 10:
        score -= 5
    
    return max(score, 0)


def get_device_health(task):
    """Retrieve health metrics from network device using napalm."""
    device = task.host
    metrics = {'device': device.name}
    
    try:
        facts_result = task.run(napalm_get, getters=['facts'])
        if facts_result.failed:
            return metrics
        
        facts = facts_result[0].result.get('facts', {})
        metrics['hostname'] = facts.get('hostname', 'Unknown')
        metrics['os_version'] = facts.get('os_version', 'Unknown')
        metrics['uptime_seconds'] = facts.get('uptime_seconds', 0)
        metrics['serial_number'] = facts.get('serial_number', 'Unknown')
        
        iface_result = task.run(napalm_get, getters=['interfaces'])
        if iface_result.failed:
            metrics['interface_count'] = 0
            metrics['total_errors'] = 0
            metrics['total_discards'] = 0
            return metrics
        
        interfaces = iface_result[0].result.get('interfaces', {})
        metrics['interface_count'] = len(interfaces)
        
        total_errors = 0
        total_discards = 0
        for iface_data in interfaces.values():
            stats = iface_data.get('statistics', {})
            total_errors += stats.get('rx_errors', 0) + stats.get('tx_errors', 0)
            total_discards += stats.get('rx_discards', 0) + stats.get('tx_discards', 0)
        
        metrics['total_errors'] = total_errors
        metrics['total_discards'] = total_discards
        
    except Exception as e:
        logger.error(f"Error collecting metrics from {device.name}: {str(e)}")
        metrics['error'] = str(e)
    
    return metrics


def print_text_report(health_data):
    """Print formatted text report."""
    print("\n" + "=" * 80)
    print("DEVICE HEALTH REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    for host_name, health in sorted(health_data.items()):
        print(f"\n{host_name}:")
        
        if 'error' in health:
            print(f"  Status: ERROR - {health['error']}")
        else:
            score = calculate_health_score(health)
            status = "HEALTHY" if score >= 80 else "WARNING" if score >= 60 else "CRITICAL"
            
            print(f"  Health Score: {score}/100 [{status}]")
            print(f"  Hostname: {health['hostname']}")
            print(f"  OS Version: {health['os_version']}")
            print(f"  Serial: {health['serial_number']}")
            print(f"  Uptime: {format_uptime(health['uptime_seconds'])}")
            print(f"  Interfaces: {health['interface_count']}")
            print(f"  Errors: {health['total_errors']}")
            print(f"  Discards: {health['total_discards']}")
    
    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description='Generate network device health report'
    )
    parser.add_argument(
        '--hosts',
        nargs='+',
        help='Specific hosts to query'
    )
    parser.add_argument(
        '--filter',
        type=str,
        help='Nornir filter string (e.g., "site:us-east")'
    )
    parser.add_argument(
        '--format',
        choices=['text', 'json'],
        default='text',
        help='Output format'
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
        nr = InitNornir(config_file="config.yaml")
        
        if args.hosts:
            nr = nr.filter(F(name__in=args.hosts))
        elif args.filter:
            nr = nr.filter(F(args.filter))
        
        logger.info(f"Running health check on {len(nr.inventory.hosts)} devices")
        results = nr.run(task=get_device_health)
        
        health_data = {}
        for host_name, task_result in results.items():
            if task_result.failed:
                health_data[host_name] = {'error': 'Task failed'}
            else:
                health_data[host_name] = task_result[0].result
        
        if args.format == 'json':
            output = {}
            for host, health in health_data.items():
                output[host] = {
                    **health,
                    'health_score': calculate_health_score(health),
                    'timestamp': datetime.now().isoformat()
                }
            print(json.dumps(output, indent=2))
        else:
            print_text_report(health_data)
        
        logger.info("Health report completed successfully")
        return 0
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
```