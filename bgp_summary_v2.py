```python
#!/usr/bin/env python3
"""
Device Facts Collector

Gathers device inventory information including serial numbers, model,
software version, interface counts, and configuration details across
multiple network devices.

Usage:
    python device_facts_collector.py --devices router1,router2
    python device_facts_collector.py --group core_routers --output json
    python device_facts_collector.py --all --export inventory.json

Prerequisites:
    - nornir with netmiko plugin
    - devices configured in inventory (hosts.yaml, groups.yaml)
    - SSH credentials configured (credentials.yaml)

Output:
    Device facts in table or JSON format with serial, model, version,
    interface count, memory, and configuration details
"""

import argparse
import logging
import json
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir.core.filter import F

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_device_facts(task: Task) -> Result:
    """Collect device facts via show commands."""
    facts = {
        'device': task.host.name,
        'ip': task.host.get('ip', 'N/A'),
        'hostname': None,
        'model': None,
        'serial': None,
        'version': None,
        'uptime': None,
        'interface_count': 0,
        'memory_total': None,
        'error': None
    }
    
    try:
        result = task.run(
            netmiko_send_command,
            command_string='show version'
        )
        version_output = result[0].result
        
        for line in version_output.split('\n'):
            if 'Cisco' in line and 'IOS' in line:
                parts = line.split()
                if len(parts) >= 2:
                    facts['model'] = parts[1]
            if 'Processor board ID' in line or 'Serial Number' in line:
                facts['serial'] = line.split(':')[-1].strip()
            if 'Software Version' in line or 'IOS Software' in line:
                facts['version'] = line.split(',')[0].strip()
            if 'uptime' in line.lower():
                facts['uptime'] = line.strip()
        
        result = task.run(
            netmiko_send_command,
            command_string='show interfaces summary'
        )
        interfaces_output = result[0].result
        
        for line in interfaces_output.split('\n'):
            if '*number of interfaces up' in line.lower():
                parts = line.split()
                if parts:
                    try:
                        facts['interface_count'] = int(parts[0])
                    except ValueError:
                        pass
        
        result = task.run(
            netmiko_send_command,
            command_string='show memory statistics'
        )
        memory_output = result[0].result
        
        for line in memory_output.split('\n'):
            if 'Total' in line and 'Processor' in line:
                parts = line.split()
                if parts:
                    try:
                        facts['memory_total'] = f"{int(parts[-2]) // 1024} MB"
                    except (ValueError, IndexError):
                        pass
        
        result = task.run(
            netmiko_send_command,
            command_string='show running-config | include hostname'
        )
        hostname_output = result[0].result.strip()
        if hostname_output:
            facts['hostname'] = hostname_output.split()[-1]
    
    except Exception as e:
        facts['error'] = str(e)
        logger.warning(f"Failed to collect facts from {task.host.name}: {e}")
    
    return Result(host=task.host, result=facts)


def format_table_output(results: Dict[str, Any]) -> None:
    """Format results as human-readable table."""
    print("\n" + "=" * 140)
    print(f"{'Device':<15} {'IP':<15} {'Model':<20} {'Serial':<20} "
          f"{'Version':<15} {'Interfaces':<12} {'Memory':<12}")
    print("-" * 140)
    
    for host, task_results in results.items():
        for task_result in task_results.values():
            if task_result.ok:
                f = task_result.result
                model = f['model'] or 'Unknown'
                serial = f['serial'] or 'N/A'
                version = f['version'] or 'N/A'
                iface_count = str(f['interface_count']) if f['interface_count'] else 'N/A'
                memory = f['memory_total'] or 'N/A'
                
                print(f"{host:<15} {f['ip']:<15} {model:<20} {serial:<20} "
                      f"{version:<15} {iface_count:<12} {memory:<12}")
            else:
                print(f"{host:<15} {'ERROR':<15} {str(task_result.exception):<20}")
    
    print("=" * 140 + "\n")


def format_json_output(results: Dict[str, Any]) -> None:
    """Format results as JSON."""
    output_data = []
    
    for host, task_results in results.items():
        for task_result in task_results.values():
            if task_result.ok:
                output_data.append(task_result.result)
    
    print(json.dumps(output_data, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description='Collect device inventory facts',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n'
               '  python %(prog)s --devices router1,router2\n'
               '  python %(prog)s --group core_routers --output json\n'
               '  python %(prog)s --all --export facts.json'
    )
    
    parser.add_argument(
        '--devices',
        help='Comma-separated list of device names'
    )
    parser.add_argument(
        '--group',
        help='Nornir group filter'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Query all devices in inventory'
    )
    parser.add_argument(
        '--output',
        choices=['table', 'json'],
        default='table',
        help='Output format (default: table)'
    )
    parser.add_argument(
        '--export',
        help='Export results to JSON file'
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file='config.yaml')
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices")
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(F(name__in=device_list))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        elif not args.all:
            logger.error("Specify --devices, --group, or --all")
            return
        
        if len(nr.inventory.hosts) == 0:
            logger.error("No devices matched filter criteria")
            return
        
        logger.info(f"Collecting facts from {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=collect_device_facts)
        
        if args.output == 'json' or args.export:
            output_data = []
            for host, task_results in results.items():
                for task_result in task_results.values():
                    if task_result.ok:
                        output_data.append(task_result.result)
            
            if args.export:
                with open(args.export, 'w') as f:
                    json.dump(output_data, f, indent=2)
                logger.info(f"Results exported to {args.export}")
            else:
                print(json.dumps(output_data, indent=2))
        else:
            format_table_output(results)
        
        logger.info("Device facts collection completed")
    
    except FileNotFoundError:
        logger.error("config.yaml not found in current directory")
    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == '__main__':
    main()
```