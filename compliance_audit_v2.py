```python
"""
Device Health Check and Monitoring Script

Purpose:
    Performs comprehensive health checks on network devices using Nornir and NAPALM.
    Gathers metrics like CPU, memory, interface status, and uptime. Reports devices
    exceeding configurable thresholds.

Usage:
    python device_health_check.py --username admin --password pass123

Prerequisites:
    - Nornir installed and configured with inventory
    - NAPALM drivers installed for target devices
    - Network connectivity to devices
"""

import argparse
import logging
import sys
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def health_check_task(task, cpu_threshold, memory_threshold):
    """Gather health metrics from device."""
    result = {
        'hostname': task.host.name,
        'status': 'healthy',
        'alerts': []
    }

    try:
        # Get facts
        facts = task.run(napalm_get, getters=['facts'])
        facts_data = facts[0].result.get('facts', {})
        result['uptime'] = facts_data.get('uptime', 'N/A')

        # Get interfaces
        interfaces = task.run(napalm_get, getters=['interfaces'])
        iface_data = interfaces[0].result.get('interfaces', {})
        down = [i for i, d in iface_data.items() if not d['is_up']]
        
        if down:
            result['alerts'].append(f"Down: {', '.join(down)}")
            result['status'] = 'warning'

        # Get environment
        environment = task.run(napalm_get, getters=['environment'])
        env_data = environment[0].result.get('environment', {})
        
        # Check CPU
        cpu = env_data.get('cpu', {})
        for name, util in cpu.items():
            cpu_val = util.get('%usage', util) if isinstance(util, dict) else util
            if cpu_val > cpu_threshold:
                result['alerts'].append(f"CPU {cpu_val}%")
                result['status'] = 'warning'

        # Check Memory
        mem = env_data.get('memory', {})
        if mem:
            used = mem.get('used_ram', 0)
            total = mem.get('available_ram', 1)
            mem_pct = (used / total * 100) if total > 0 else 0
            if mem_pct > memory_threshold:
                result['alerts'].append(f"Memory {mem_pct:.0f}%")
                result['status'] = 'warning'

    except Exception as e:
        logger.error(f"{task.host.name}: {e}")
        result['status'] = 'error'
        result['alerts'] = [str(e)]

    return result


def run_health_checks(nr, device_filter=None, cpu_thr=80, mem_thr=85):
    """Run health checks on devices."""
    hosts = nr.inventory.hosts
    
    if device_filter:
        hosts = {k: v for k, v in hosts.items() if device_filter in k}
    
    if not hosts:
        logger.warning("No devices matched filter")
        return []
    
    logger.info(f"Checking {len(hosts)} device(s)")
    results = []
    
    for hostname in hosts:
        filtered = nr.filter(F(name=hostname))
        try:
            task_results = filtered.run(
                task=health_check_task,
                cpu_threshold=cpu_thr,
                memory_threshold=mem_thr
            )
            
            for host_name, host_result in task_results.items():
                for task_name, task_obj in host_result.items():
                    if hasattr(task_obj, 'result'):
                        results.append(task_obj.result)
                        logger.info(f"{host_name}: {task_obj.result['status']}")
        except Exception as e:
            logger.error(f"Failed to check {hostname}: {e}")
            results.append({
                'hostname': hostname,
                'status': 'error',
                'alerts': [str(e)]
            })
    
    return results


def print_report(results):
    """Print health report."""
    print("\n" + "=" * 65)
    print(f"{'Device':<20} {'Status':<12} {'Issues':<32}")
    print("=" * 65)
    
    for r in results:
        alerts = ', '.join(r.get('alerts', [])) or 'None'
        print(f"{r['hostname']:<20} {r['status'].upper():<12} {alerts[:31]:<32}")
    
    print("=" * 65)
    healthy = len([r for r in results if r['status'] == 'healthy'])
    warning = len([r for r in results if r['status'] == 'warning'])
    error = len([r for r in results if r['status'] == 'error'])
    print(f"Summary: {healthy} healthy, {warning} warnings, {error} errors\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Network device health monitoring'
    )
    parser.add_argument('--username', required=True, help='Device username')
    parser.add_argument('--password', required=True, help='Device password')
    parser.add_argument('--device-filter', help='Filter devices by name')
    parser.add_argument(
        '--cpu-threshold',
        type=int,
        default=80,
        help='CPU utilization threshold (default: 80)'
    )
    parser.add_argument(
        '--memory-threshold',
        type=int,
        default=85,
        help='Memory utilization threshold (default: 85)'
    )
    parser.add_argument('--verbose', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file='config.yaml')
        
        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password
        
        results = run_health_checks(
            nr,
            device_filter=args.device_filter,
            cpu_thr=args.cpu_threshold,
            mem_thr=args.memory_threshold
        )
        
        print_report(results)
        
        errors = len([r for r in results if r['status'] == 'error'])
        warnings = len([r for r in results if r['status'] == 'warning'])
        
        sys.exit(2 if errors else (1 if warnings else 0))
    
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(2)
```