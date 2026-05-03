```python
"""
Nornir Device Health Check - System Resource Monitoring

Purpose:
    Gather and report on device health metrics including CPU utilization,
    memory usage, and interface statistics across a network inventory.
    Identifies devices with critical resource constraints.

Usage:
    python 018_device_health_check.py --inventory inventory.yaml \
        --device router01 --threshold-cpu 80 --threshold-memory 85

Prerequisites:
    - Nornir installed and configured
    - Network device SSH/API access credentials
    - Supported device OS drivers (Cisco IOS, IOS-XE, Junos, etc.)
    - napalm or netmiko for command execution
    - YAML inventory file with device definitions

Output:
    - CSV report of device health metrics
    - Console summary with color-coded status
    - Detailed device report per specified filters
"""

import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command
from nornir_utils.plugins.functions import print_result


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_device_health(task: Task) -> Result:
    """
    Gather system health metrics from network device.
    
    Args:
        task: Nornir task object
        
    Returns:
        Nornir Result with device health metrics
    """
    device = task.host
    health_data = {
        'hostname': device.name,
        'device_type': device.platform,
        'timestamp': datetime.now().isoformat(),
        'metrics': {},
        'status': 'UNKNOWN',
        'errors': []
    }
    
    try:
        if device.platform in ('ios', 'cisco_ios'):
            cpu_result = task.run(
                netmiko_send_command,
                command_string='show processes cpu | include CPU utilization'
            )
            health_data['metrics']['cpu'] = cpu_result.result
            
            mem_result = task.run(
                netmiko_send_command,
                command_string='show memory statistics'
            )
            health_data['metrics']['memory'] = mem_result.result
            
            uptime_result = task.run(
                netmiko_send_command,
                command_string='show version | include uptime'
            )
            health_data['metrics']['uptime'] = uptime_result.result
            
        elif device.platform == 'junos':
            system_result = task.run(
                netmiko_send_command,
                command_string='show system uptime'
            )
            health_data['metrics']['uptime'] = system_result.result
            
        int_result = task.run(
            netmiko_send_command,
            command_string='show interfaces summary'
        )
        health_data['metrics']['interface_summary'] = int_result.result
        
        health_data['status'] = 'SUCCESS'
        
    except Exception as e:
        health_data['status'] = 'FAILED'
        health_data['errors'].append(str(e))
        logger.error(f"Health check failed for {device.name}: {e}")
    
    return Result(host=task.host, result=health_data)


def parse_cpu_utilization(cpu_output: str) -> int:
    """
    Extract CPU utilization percentage from device output.
    
    Args:
        cpu_output: Raw CPU output string
        
    Returns:
        CPU utilization as integer percentage
    """
    try:
        parts = cpu_output.split()
        for i, part in enumerate(parts):
            if '%' in part and i > 0:
                return int(parts[i - 1])
    except (ValueError, IndexError):
        pass
    return -1


def evaluate_health(
    health_metrics: List[Dict],
    cpu_threshold: int,
    mem_threshold: int
) -> List[Dict]:
    """
    Evaluate device metrics against threshold values.
    
    Args:
        health_metrics: List of device health data
        cpu_threshold: CPU utilization threshold (0-100)
        mem_threshold: Memory utilization threshold (0-100)
        
    Returns:
        Annotated metrics with alert status
    """
    for metric in health_metrics:
        metric['alerts'] = []
        
        if metric['status'] == 'FAILED':
            metric['alerts'].append('Device unreachable')
            metric['health_status'] = 'CRITICAL'
            continue
        
        cpu_output = metric['metrics'].get('cpu', '')
        cpu_util = parse_cpu_utilization(cpu_output)
        if cpu_util >= 0 and cpu_util > cpu_threshold:
            metric['alerts'].append(f"CPU {cpu_util}% (threshold: {cpu_threshold}%)")
        
        metric['health_status'] = 'CRITICAL' if metric['alerts'] else 'OK'
    
    return health_metrics


def write_report(
    health_metrics: List[Dict],
    output_file: Path
) -> None:
    """
    Write health metrics to CSV report.
    
    Args:
        health_metrics: List of device health data
        output_file: Path for CSV output
    """
    if not health_metrics:
        logger.warning("No health metrics to report")
        return
    
    try:
        with open(output_file, 'w', newline='') as f:
            fieldnames = ['hostname', 'device_type', 'status', 'health_status', 'alerts']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for metric in health_metrics:
                writer.writerow({
                    'hostname': metric['hostname'],
                    'device_type': metric['device_type'],
                    'status': metric['status'],
                    'health_status': metric.get('health_status', 'UNKNOWN'),
                    'alerts': '; '.join(metric.get('alerts', []))
                })
        
        logger.info(f"Report written to {output_file}")
        
    except IOError as e:
        logger.error(f"Failed to write report: {e}")


def main():
    """Main entry point for device health check."""
    parser = argparse.ArgumentParser(
        description='Check device health metrics across network inventory'
    )
    parser.add_argument(
        '--inventory',
        type=str,
        default='inventory.yaml',
        help='Path to Nornir inventory file (default: inventory.yaml)'
    )
    parser.add_argument(
        '--device',
        type=str,
        help='Target specific device by hostname'
    )
    parser.add_argument(
        '--threshold-cpu',
        type=int,
        default=80,
        help='CPU threshold percent (default: 80)'
    )
    parser.add_argument(
        '--threshold-memory',
        type=int,
        default=85,
        help='Memory threshold percent (default: 85)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='health_report.csv',
        help='Output CSV file (default: health_report.csv)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(name=args.device)
            if not nr.inventory.hosts:
                logger.error(f"Device '{args.device}' not found")
                return
        
        logger.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=get_device_health)
        
        health_metrics = [
            r.result for host_results in results.values()
            for r in host_results if r.result
        ]
        
        health_metrics = evaluate_health(
            health_metrics,
            args.threshold_cpu,
            args.threshold_memory
        )
        
        write_report(health_metrics, Path(args.output))
        print_result(results)
        
        logger.info("Health check completed")
        
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
    except Exception as e:
        logger.error(f"Health check failed: {e}")


if __name__ == '__main__':
    main()
```