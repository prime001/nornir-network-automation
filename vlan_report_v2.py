```python
"""
Network Device Health Check

Purpose: Performs health checks on network devices including CPU usage, memory
utilization, uptime, and interface operational status.

Usage:
    python health_check.py --devices router1 router2
    python health_check.py --filter "site:dc1" --threshold-cpu 75 --format json
    python health_check.py --all --output report.txt

Prerequisites:
    - Nornir configured with inventory (config.yaml)
    - Network devices accessible via SSH
    - NAPALM installed for device interaction
    - Credentials in environment variables or .env file
"""

import logging
import argparse
import sys
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_metrics(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key health metrics from device facts."""
    
    metrics = {
        'hostname': facts.get('hostname', 'unknown'),
        'uptime_seconds': facts.get('uptime_seconds', 0),
        'interfaces_up': 0,
        'interfaces_down': 0,
    }
    
    if 'interface_list' in facts:
        metrics['total_interfaces'] = len(facts['interface_list'])
    
    return metrics


def parse_interfaces(iface_data: Dict[str, Any]) -> Dict[str, int]:
    """Count operational vs down interfaces."""
    
    status = {'up': 0, 'down': 0}
    
    for iface, details in iface_data.items():
        if details.get('is_up', False):
            status['up'] += 1
        else:
            status['down'] += 1
    
    return status


def health_check(task: Task, cpu_threshold: int, mem_threshold: int) -> Result:
    """Execute health check on device using NAPALM."""
    
    try:
        facts_result = task.run(
            napalm_get,
            getters=['facts', 'interfaces', 'environment'],
            name='get_facts'
        )
        
        data = facts_result.result
        facts = data.get('facts', {})
        
        health = {
            'device': facts.get('hostname', task.host.name),
            'status': 'healthy',
            'issues': [],
            'uptime_hours': int(facts.get('uptime_seconds', 0) / 3600),
        }
        
        interfaces = data.get('interfaces', {})
        iface_status = parse_interfaces(interfaces)
        health['interfaces_up'] = iface_status['up']
        health['interfaces_down'] = iface_status['down']
        
        if iface_status['down'] > 0:
            health['issues'].append(
                f"{iface_status['down']} interface(s) down"
            )
        
        environment = data.get('environment', {})
        cpu_usage = environment.get('cpu', {}).get('0', {}).get('%usage', 0)
        
        if cpu_usage > cpu_threshold:
            health['status'] = 'warning'
            health['cpu_usage'] = cpu_usage
            health['issues'].append(f"CPU usage {cpu_usage}% exceeds threshold {cpu_threshold}%")
        
        mem = environment.get('memory', {})
        if mem:
            mem_percent = int((mem.get('used_ram', 0) / mem.get('available_ram', 1)) * 100)
            if mem_percent > mem_threshold:
                health['status'] = 'warning'
                health['memory_usage'] = mem_percent
                health['issues'].append(
                    f"Memory usage {mem_percent}% exceeds threshold {mem_threshold}%"
                )
        
        if not health['issues']:
            health['status'] = 'healthy'
        
        return Result(host=task.host, result=health)
        
    except Exception as e:
        logger.error(f"{task.host.name}: {str(e)}")
        return Result(
            host=task.host,
            failed=True,
            result={'device': task.host.name, 'error': str(e)}
        )


def format_output(results: Dict, output_format: str) -> str:
    """Format health check results for display."""
    
    if output_format == 'json':
        import json
        return json.dumps(
            {host: result[0].result for host, result in results.items()},
            indent=2
        )
    
    lines = []
    for host in sorted(results.keys()):
        result = results[host][0].result
        
        if 'error' in result:
            lines.append(f"[ERROR] {result['device']}: {result['error']}")
            continue
        
        status_icon = '✓' if result['status'] == 'healthy' else '⚠'
        lines.append(f"{status_icon} {result['device']} ({result['status'].upper()})")
        lines.append(f"  Uptime: {result['uptime_hours']}h")
        lines.append(f"  Interfaces: {result['interfaces_up']}↑ {result['interfaces_down']}↓")
        
        if result.get('cpu_usage') is not None:
            lines.append(f"  CPU: {result['cpu_usage']}%")
        if result.get('memory_usage') is not None:
            lines.append(f"  Memory: {result['memory_usage']}%")
        
        if result['issues']:
            for issue in result['issues']:
                lines.append(f"    ⚠ {issue}")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Health check for network devices',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--devices', nargs='+', help='Device names to check')
    parser.add_argument('--filter', help='Filter by attribute (e.g., "site:dc1")')
    parser.add_argument('--all', action='store_true', help='Check all devices')
    parser.add_argument('--threshold-cpu', type=int, default=80, help='CPU threshold %')
    parser.add_argument('--threshold-mem', type=int, default=85, help='Memory threshold %')
    parser.add_argument(
        '--format',
        choices=['text', 'json'],
        default='text',
        help='Output format'
    )
    parser.add_argument('--output', help='Save report to file')
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file='config.yaml')
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return 1
    
    if args.devices:
        nr = nr.filter(name__in=args.devices)
    elif args.filter:
        key, value = args.filter.split(':')
        nr = nr.filter(**{key: value})
    elif not args.all:
        logger.error("Specify --devices, --filter, or --all")
        return 1
    
    try:
        results = nr.run(
            task=health_check,
            cpu_threshold=args.threshold_cpu,
            mem_threshold=args.threshold_mem,
            name='health_check'
        )
        
        output = format_output(results, args.format)
        print(output)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            logger.info(f"Report saved to {args.output}")
        
        failed_hosts = [host for host, result in results.items() if result.failed]
        if failed_hosts:
            logger.warning(f"Failed on: {', '.join(failed_hosts)}")
            return 1
        
        return 0
        
    except Exception as e:
        logger.error(f"Health check execution failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```