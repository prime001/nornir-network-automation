```python
"""
Device Health and Performance Monitoring

Purpose:
    Collects and reports on device health metrics including CPU, memory,
    temperature, and uptime across network inventory. Useful for operations
    teams performing proactive monitoring and capacity planning.

Usage:
    python device_health_check.py --inventory inventory.yaml --user admin
    python device_health_check.py --device router1 --user admin --output json
    python device_health_check.py --group core --user admin --verbose

Prerequisites:
    - nornir with napalm plugin (pip install nornir-napalm)
    - Network devices must support NAPALM
    - SSH/NETCONF access configured
    - Credentials: use --password or set NORNIR_PASSWORD environment variable
"""

import argparse
import json
import logging
import sys
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def collect_health_metrics(task: Task) -> Result:
    """
    Gather health metrics from device using NAPALM getters.
    
    Returns health data or None on failure.
    """
    try:
        r = task.run(
            napalm_get,
            getters=['get_facts', 'get_environment']
        )
        
        facts = r[0].result.get('get_facts', {})
        env = r[0].result.get('get_environment', {})
        
        health = {
            'hostname': facts.get('hostname'),
            'vendor': facts.get('vendor'),
            'model': facts.get('model'),
            'os_version': facts.get('os_version'),
            'uptime_seconds': facts.get('uptime_seconds'),
            'serial': facts.get('serial_number'),
        }
        
        cpu_data = env.get('cpu', {})
        if isinstance(cpu_data, dict):
            cpu_list = [v.get('%usage', 0) for v in cpu_data.values()]
            health['cpu_percent'] = sum(cpu_list) / len(cpu_list) if cpu_list else 0
        
        mem_data = env.get('memory', {})
        if mem_data:
            used = mem_data.get('used_ram', 0)
            avail = mem_data.get('available_ram', 0)
            total = used + avail
            health['memory_percent'] = (used / total * 100) if total > 0 else 0
        
        temp_data = env.get('temperature', {})
        if isinstance(temp_data, dict):
            temps = [v.get('current_temperature', 0) for v in temp_data.values()]
            health['temp_celsius'] = sum(temps) / len(temps) if temps else 0
        
        return Result(host=task.host, result=health)
        
    except Exception as e:
        logger.error(f"{task.host}: {str(e)}")
        return Result(host=task.host, result=None, failed=True)


def format_uptime(seconds: int) -> str:
    """Convert seconds to human-readable uptime."""
    if not seconds:
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60
    return f"{days}d {hours}h {mins}m"


def print_text_report(data: Dict[str, Any]) -> None:
    """Print formatted health report."""
    print("\n" + "=" * 90)
    print("DEVICE HEALTH REPORT")
    print("=" * 90)
    print(f"{'Hostname':<20} {'Model':<20} {'Uptime':<15} {'CPU%':<8} {'Mem%':<8} {'Temp°C':<8}")
    print("-" * 90)
    
    for hostname, health in sorted(data.items()):
        if health is None:
            print(f"{hostname:<20} {'FAILED':<20}")
            continue
        
        uptime_str = format_uptime(health.get('uptime_seconds', 0))
        model = health.get('model', 'N/A')[:19]
        cpu = health.get('cpu_percent', 0)
        mem = health.get('memory_percent', 0)
        temp = health.get('temp_celsius', 0)
        
        print(f"{hostname:<20} {model:<20} {uptime_str:<15} {cpu:<8.1f} {mem:<8.1f} {temp:<8.1f}")
    
    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor network device health and performance',
        epilog='Example: python device_health_check.py --inventory inventory.yaml --user admin'
    )
    
    parser.add_argument('--inventory', default='inventory.yaml',
                        help='Nornir inventory file path')
    parser.add_argument('--user', required=True, help='Device username')
    parser.add_argument('--password', help='Device password (or use NORNIR_PASSWORD env var)')
    parser.add_argument('--device', help='Filter by specific hostname')
    parser.add_argument('--group', help='Filter by inventory group')
    parser.add_argument('--output', choices=['text', 'json'], default='text',
                        help='Output format')
    parser.add_argument('--json-file', default='health_report.json',
                        help='JSON output filename')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(name=args.device)
            logger.info(f"Filtered to device: {args.device}")
        elif args.group:
            nr = nr.filter(group__name=args.group)
            logger.info(f"Filtered to group: {args.group}")
        
        logger.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=collect_health_metrics)
        
        health_data = {}
        for hostname in results:
            if results[hostname][0].result:
                health_data[hostname] = results[hostname][0].result
            else:
                health_data[hostname] = None
        
        if args.output == 'text':
            print_text_report(health_data)
        else:
            with open(args.json_file, 'w') as f:
                json.dump(health_data, f, indent=2, default=str)
            logger.info(f"Health report saved to {args.json_file}")
        
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```