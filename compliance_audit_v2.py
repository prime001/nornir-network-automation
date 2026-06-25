```python
#!/usr/bin/env python3
"""
Device Health Audit - Network Device Status and Resource Monitoring

Audits device health metrics across the network inventory using Nornir.
Collects uptime, system resources, and connectivity status to identify
at-risk or degraded devices.

Usage:
    python3 059_device_health_audit.py --username admin --password secret
    python3 059_device_health_audit.py -u admin -p secret --device core-rtr-1
    python3 059_device_health_audit.py -u admin -p secret --metric uptime --metric memory

Prerequisites:
    - nornir[netmiko] installed
    - Inventory YAML configured (hosts.yaml, groups.yaml, defaults.yaml)
    - Devices reachable via SSH and configured for CLI access
    - Device OS support: Cisco IOS, IOS-XE
"""

import argparse
import logging
import sys
from typing import Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result


logger = logging.getLogger(__name__)


def collect_uptime(task: Task) -> Result:
    """Collect device uptime via show version command."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string='show version | include uptime'
        )
        uptime_line = result[0].result.strip()
        return Result(host=task.host, result={'uptime': uptime_line or 'N/A'})
    except Exception as e:
        logger.warning(f"{task.host.name}: uptime collection failed - {e}")
        return Result(host=task.host, result={'uptime': 'ERROR'}, failed=True)


def collect_memory_usage(task: Task) -> Result:
    """Collect device memory utilization statistics."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string='show processes memory | include Memory'
        )
        mem_line = result[0].result.strip() or 'No data'
        return Result(host=task.host, result={'memory': mem_line})
    except Exception as e:
        logger.warning(f"{task.host.name}: memory collection failed - {e}")
        return Result(host=task.host, result={'memory': 'ERROR'}, failed=True)


def test_connectivity(task: Task, target: str = '8.8.8.8') -> Result:
    """Test outbound connectivity via ping."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string=f'ping {target} repeat 2 timeout 1'
        )
        output = result[0].result
        status = 'OK' if '0% loss' in output or 'success' in output.lower() else 'FAIL'
        return Result(host=task.host, result={'connectivity': status})
    except Exception as e:
        logger.warning(f"{task.host.name}: connectivity test failed - {e}")
        return Result(host=task.host, result={'connectivity': 'ERROR'}, failed=True)


def check_cpu_load(task: Task) -> Result:
    """Collect device CPU utilization."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string='show processes cpu | include CPU'
        )
        cpu_line = result[0].result.strip() or 'N/A'
        return Result(host=task.host, result={'cpu': cpu_line})
    except Exception as e:
        logger.warning(f"{task.host.name}: CPU collection failed - {e}")
        return Result(host=task.host, result={'cpu': 'ERROR'}, failed=True)


def health_audit(
    task: Task,
    metrics: Optional[list] = None
) -> Result:
    """Execute health audit tasks based on requested metrics."""
    if metrics is None:
        metrics = ['uptime', 'memory', 'cpu', 'connectivity']
    
    audit_results = {}
    
    if 'uptime' in metrics:
        r = task.run(collect_uptime)
        audit_results.update(r[0].result)
    
    if 'memory' in metrics:
        r = task.run(collect_memory_usage)
        audit_results.update(r[0].result)
    
    if 'cpu' in metrics:
        r = task.run(check_cpu_load)
        audit_results.update(r[0].result)
    
    if 'connectivity' in metrics:
        r = task.run(test_connectivity)
        audit_results.update(r[0].result)
    
    return Result(host=task.host, result=audit_results)


def main():
    parser = argparse.ArgumentParser(
        description='Audit device health and resource metrics across network inventory',
        epilog='Example: python3 059_device_health_audit.py -u admin -p secret --device core-1'
    )
    parser.add_argument(
        '-u', '--username',
        required=True,
        help='SSH username for device login'
    )
    parser.add_argument(
        '-p', '--password',
        required=True,
        help='SSH password for device login'
    )
    parser.add_argument(
        '--device',
        help='Target specific device (omit to audit entire inventory)'
    )
    parser.add_argument(
        '--metric',
        action='append',
        dest='metrics',
        choices=['uptime', 'memory', 'cpu', 'connectivity'],
        help='Specific metrics to collect (can repeat; default is all)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose debug logging'
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    try:
        nr = InitNornir(config_file='config.yaml')
        
        if args.device:
            nr = nr.filter(name=args.device)
            if not nr.inventory.hosts:
                logger.error(f"Device '{args.device}' not found in inventory")
                return 1
        
        metrics = args.metrics if args.metrics else ['uptime', 'memory', 'cpu', 'connectivity']
        logger.info(f"Auditing {len(nr.inventory.hosts)} device(s) - metrics: {metrics}")
        
        results = nr.run(task=health_audit, metrics=metrics)
        print_result(results)
        
        failed_count = sum(1 for host_result in results.values() if host_result.failed)
        logger.info(f"Audit complete: {len(nr.inventory.hosts)} devices, {failed_count} failures")
        
        return 0 if failed_count == 0 else 1
    
    except FileNotFoundError:
        logger.error("config.yaml not found. Ensure Nornir configuration exists.")
        return 2
    except Exception as e:
        logger.error(f"Audit failed: {e}", exc_info=args.verbose)
        return 2


if __name__ == '__main__':
    sys.exit(main())
```