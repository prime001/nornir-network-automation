```python
#!/usr/bin/env python3
"""
Interface Utilization & Performance Monitor

Collects real-time interface utilization metrics, identifies congested or
problematic interfaces, and generates performance audit reports with
actionable alerts for network optimization.

Purpose:
    Monitor interface bandwidth utilization and error rates across network
    devices, generating alerts when interfaces exceed configured thresholds.

Usage:
    python3 interface_monitor.py -u admin -p password --device router1
    python3 interface_monitor.py -u admin -p password --utilization-threshold 80
    python3 interface_monitor.py -u admin -p password --output metrics.json --format json

Prerequisites:
    - nornir and netmiko installed
    - SSH access to network devices
    - Credentials with read permissions
    - Target devices reachable and responding

CLI Arguments:
    -u, --username: Device username (required)
    -p, --password: Device password (required)
    --device: Target device name (default: all)
    --utilization-threshold: Alert threshold for interface utilization % (default: 80)
    --error-threshold: Alert threshold for error rate % (default: 1)
    --output: Output file path (optional, default: stdout)
    --format: Output format json or text (default: text)

"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_interface_metrics(task: Task) -> Result:
    """Collect interface utilization metrics from device."""
    metrics = {'interfaces': [], 'timestamp': '', 'alerts': []}
    
    try:
        result = task.run(
            netmiko_send_command,
            command_string='show interfaces',
            use_textfsm=False
        )
        output = result[0].result if result else ''
        
        interfaces = _parse_interface_output(output)
        metrics['interfaces'] = interfaces
        
    except Exception as e:
        logger.warning(f"Error collecting metrics from {task.host.name}: {e}")
    
    return Result(host=task.host, result=metrics)


def _parse_interface_output(output: str) -> List[Dict[str, Any]]:
    """Parse interface output into structured format."""
    interfaces = []
    current_iface = None
    
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        if line[0] not in (' ', '\t'):
            if current_iface:
                interfaces.append(current_iface)
            
            parts = line.split()
            current_iface = {
                'name': parts[0],
                'status': '',
                'protocol': '',
                'input_packets': 0,
                'output_packets': 0,
                'input_errors': 0,
                'output_errors': 0,
                'input_rate': 0,
                'output_rate': 0
            }
        
        elif current_iface:
            if 'is' in line and ('up' in line or 'down' in line):
                parts = line.split()
                current_iface['status'] = parts[1] if len(parts) > 1 else 'unknown'
                if len(parts) > 3:
                    current_iface['protocol'] = parts[3]
            
            if 'packets input' in line:
                try:
                    parts = line.split(',')
                    current_iface['input_packets'] = int(parts[0].split()[0])
                    if len(parts) > 1:
                        current_iface['input_errors'] = int(parts[1].split()[0])
                except (ValueError, IndexError):
                    pass
            
            if 'packets output' in line:
                try:
                    parts = line.split(',')
                    current_iface['output_packets'] = int(parts[0].split()[0])
                    if len(parts) > 1:
                        current_iface['output_errors'] = int(parts[1].split()[0])
                except (ValueError, IndexError):
                    pass
            
            if 'bits/sec' in line:
                try:
                    if 'input rate' in line:
                        current_iface['input_rate'] = int(line.split()[4])
                    if 'output rate' in line:
                        current_iface['output_rate'] = int(line.split()[4])
                except (ValueError, IndexError):
                    pass
    
    if current_iface:
        interfaces.append(current_iface)
    
    return interfaces


def analyze_metrics(
    metrics: Dict[str, Any],
    util_threshold: float,
    error_threshold: float,
    max_bandwidth: float = 1000000000
) -> Dict[str, Any]:
    """Analyze metrics and generate alerts."""
    analysis = {
        'total_interfaces': 0,
        'up_interfaces': 0,
        'down_interfaces': 0,
        'high_utilization': [],
        'high_error_rate': [],
        'summary': {}
    }
    
    interfaces = metrics.get('interfaces', [])
    analysis['total_interfaces'] = len(interfaces)
    
    for iface in interfaces:
        if iface.get('status', '').lower() == 'up':
            analysis['up_interfaces'] += 1
        else:
            analysis['down_interfaces'] += 1
        
        input_rate = iface.get('input_rate', 0)
        output_rate = iface.get('output_rate', 0)
        max_rate = max(input_rate, output_rate)
        
        if max_bandwidth > 0:
            utilization = (max_rate / max_bandwidth) * 100
            if utilization > util_threshold:
                analysis['high_utilization'].append({
                    'interface': iface['name'],
                    'utilization_percent': round(utilization, 2),
                    'input_rate': input_rate,
                    'output_rate': output_rate
                })
        
        total_packets = iface.get('input_packets', 0) + iface.get('output_packets', 0)
        total_errors = iface.get('input_errors', 0) + iface.get('output_errors', 0)
        
        if total_packets > 100000:
            error_rate = (total_errors / total_packets) * 100
            if error_rate > error_threshold:
                analysis['high_error_rate'].append({
                    'interface': iface['name'],
                    'error_rate_percent': round(error_rate, 4),
                    'total_errors': total_errors,
                    'total_packets': total_packets
                })
    
    analysis['summary'] = {
        'up_count': analysis['up_interfaces'],
        'down_count': analysis['down_interfaces'],
        'high_util_count': len(analysis['high_utilization']),
        'high_error_count': len(analysis['high_error_rate'])
    }
    
    return analysis


def monitor_interfaces(
    task: Task,
    util_threshold: float,
    error_threshold: float
) -> Result:
    """Main task: collect metrics and analyze."""
    metrics_result = task.run(collect_interface_metrics)
    metrics = metrics_result[0].result
    
    analysis = analyze_metrics(
        metrics,
        util_threshold=util_threshold,
        error_threshold=error_threshold
    )
    
    return Result(host=task.host, result=analysis)


def format_text_report(results: Dict[str, Any]) -> str:
    """Format results as human-readable report."""
    lines = ["Interface Utilization & Performance Report\n"]
    lines.append("=" * 70)
    
    for device_name, device_data in results.items():
        lines.append(f"\nDevice: {device_name}")
        lines.append("-" * 70)
        
        summary = device_data.get('summary', {})
        lines.append(f"Interfaces: {summary.get('up_count', 0)} up, "
                    f"{summary.get('down_count', 0)} down")
        lines.append(f"High Utilization: {summary.get('high_util_count', 0)} | "
                    f"High Error Rate: {summary.get('high_error_count', 0)}")
        
        if device_data.get('high_utilization'):
            lines.append("\nHigh Utilization Interfaces:")
            for iface in device_data['high_utilization']:
                lines.append(f"  {iface['interface']}: "
                           f"{iface['utilization_percent']}% utilization")
        
        if device_data.get('high_error_rate'):
            lines.append("\nHigh Error Rate Interfaces:")
            for iface in device_data['high_error_rate']:
                lines.append(f"  {iface['interface']}: "
                           f"{iface['error_rate_percent']}% error rate "
                           f"({iface['total_errors']} errors)")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Interface Utilization & Performance Monitor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument('-u', '--username', required=True, help='Device username')
    parser.add_argument('-p', '--password', required=True, help='Device password')
    parser.add_argument('--device', default='all', help='Target device (default: all)')
    parser.add_argument('--utilization-threshold', type=float, default=80,
                       help='Utilization alert threshold % (default: 80)')
    parser.add_argument('--error-threshold', type=float, default=1,
                       help='Error rate alert threshold % (default: 1)')
    parser.add_argument('--output', type=Path, help='Output file (optional)')
    parser.add_argument('--format', choices=['json', 'text'], default='text',
                       help='Output format (default: text)')
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.device != 'all':
            nr = nr.filter(name=args.device)
        
        if not nr.inventory.hosts:
            logger.error(f"No hosts found for device: {args.device}")
            sys.exit(1)
        
        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password
        
        logger.info(f"Monitoring {len(nr.inventory.hosts)} device(s)...")
        
        results = nr.run(
            task=monitor_interfaces,
            util_threshold=args.utilization_threshold,
            error_threshold=args.error_threshold
        )
        
        output_data = {
            device: task[0].result
            for device, task in results.items()
            if task[0].result
        }
        
        if args.format == 'json':
            output = json.dumps(output_data, indent=2)
        else:
            output = format_text_report(output_data)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            logger.info(f"Report saved to {args.output}")
        else:
            print(output)
        
        logger.info("Monitor completed successfully")
    
    except Exception as e:
        logger.error(f"Monitor failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
```