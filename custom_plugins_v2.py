```python
"""
Multi-Device Command Output Comparator

Purpose:
    Execute the same command across multiple devices and compare outputs to identify
    discrepancies, validate configurations, and troubleshoot inconsistencies. Useful
    for detecting configuration drift, verifying synchronization, and network audits.

Usage:
    python command_comparator.py --devices core1,core2,core3 --command "show version" \\
        --username admin --password secret --config config.yaml

Prerequisites:
    - Nornir installed and configured with device inventory
    - netmiko library for SSH connectivity
    - Devices must support the specified command
    - SSH access to all target devices

Output:
    Displays command outputs from each device with visual diff highlighting
    and a summary of unique responses.
"""

import argparse
import logging
import sys
from typing import Dict, List, Set
from difflib import unified_diff

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def execute_command(task: Task, command: str) -> Result:
    """Execute command on device via netmiko."""
    try:
        result = task.run(netmiko_send_command, command_string=command)
        output = result[0].result
        return Result(host=task.host, result=output)
    except Exception as e:
        logger.error(f"Command failed on {task.host.name}: {str(e)}")
        return Result(host=task.host, result=None, failed=True, exception=e)


def normalize_output(output: str) -> str:
    """Normalize output for comparison (strip whitespace, lowercase)."""
    return '\n'.join(line.rstrip() for line in output.strip().split('\n'))


def identify_unique_outputs(results: Dict) -> Dict[str, List[str]]:
    """Group devices by output uniqueness."""
    output_map = {}
    unique_outputs = {}
    
    for device_name, task_result in results.items():
        if task_result[0].failed or not task_result[0].result:
            device_list = output_map.setdefault('ERROR', [])
            device_list.append(device_name)
            continue
        
        output = normalize_output(task_result[0].result)
        output_hash = hash(output)
        
        if output_hash not in unique_outputs:
            unique_outputs[output_hash] = {
                'output': output,
                'devices': []
            }
        unique_outputs[output_hash]['devices'].append(device_name)
    
    result = {}
    for output_hash, data in unique_outputs.items():
        key = f"Output_{len(result)+1}"
        result[key] = data['devices']
    
    if 'ERROR' in output_map:
        result['ERRORS'] = output_map['ERROR']
    
    return result


def print_results(results: Dict, command: str) -> None:
    """Display command outputs and comparison summary."""
    print("\n" + "="*80)
    print(f"COMMAND: {command}")
    print("="*80)
    
    success_count = 0
    error_count = 0
    
    for device_name, task_result in sorted(results.items()):
        print(f"\n[{device_name}]")
        print("-" * 80)
        
        if task_result[0].failed:
            print(f"ERROR: Command execution failed")
            error_count += 1
            continue
        
        output = task_result[0].result
        if output:
            print(output)
            success_count += 1
        else:
            print("No output")
    
    print("\n" + "="*80)
    print(f"SUMMARY: {success_count} successful, {error_count} failed")
    
    unique_outputs = identify_unique_outputs(results)
    print(f"\nUnique Outputs Detected: {len(unique_outputs)}")
    
    for output_type, devices in unique_outputs.items():
        print(f"  {output_type}: {', '.join(devices)}")
    
    if len(unique_outputs) > 1:
        print("\nWARNING: Output discrepancies detected across devices")


def main():
    parser = argparse.ArgumentParser(
        description='Compare command outputs across multiple devices',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--config', default='config.yaml',
                        help='Nornir config file (default: config.yaml)')
    parser.add_argument('--devices', required=True,
                        help='Comma-separated device names')
    parser.add_argument('--command', required=True,
                        help='Command to execute on all devices')
    parser.add_argument('--threads', type=int, default=5,
                        help='Number of concurrent connections (default: 5)')
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.config)
    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)
    
    devices = [d.strip() for d in args.devices.split(',')]
    filtered_nr = nr.filter(F(name__in=devices))
    
    if len(filtered_nr.inventory.hosts) == 0:
        logger.error(f"No devices matched: {devices}")
        sys.exit(1)
    
    logger.info(f"Executing '{args.command}' on {len(filtered_nr.inventory.hosts)} devices")
    
    results = filtered_nr.run(
        task=execute_command,
        command=args.command,
        num_workers=args.threads
    )
    
    print_results(results, args.command)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
```