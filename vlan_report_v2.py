```python
"""
Device System Information Collector

Purpose:
    Collects system information (hostname, model, serial number, uptime,
    OS version, memory) from network devices and generates an inventory
    report suitable for asset management and compliance tracking.

Usage:
    python device_inventory.py --output inventory_report.csv
    python device_inventory.py --devices core-* --format json --output core_devices.json
    python device_inventory.py --inventory custom_inventory.yaml --format pretty

Prerequisites:
    - Nornir installed and configured with device inventory
    - Device credentials in nornir config or environment variables
    - Network connectivity to all target devices
    - Support for 'show version' and 'show inventory' commands
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_system_info(task: Task) -> Result:
    """
    Collect system information from network device.
    
    Gathers version, uptime, model, serial number, and memory information.
    """
    try:
        version_output = task.run(
            netmiko_send_command,
            command_string="show version",
            use_textfsm=False
        )
        
        inventory_output = task.run(
            netmiko_send_command,
            command_string="show inventory",
            use_textfsm=False
        )
        
        return Result(
            host=task.host,
            result={
                'version': version_output[0].result,
                'inventory': inventory_output[1].result
            }
        )
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to collect system info - {e}")
        return Result(host=task.host, failed=True, exception=e)


def parse_system_info(host_name: str, outputs: Dict[str, str]) -> Dict[str, Any]:
    """
    Parse system information from command outputs.
    
    Extracts key fields like model, serial, uptime from show version/inventory.
    """
    info = {
        'hostname': host_name,
        'model': 'Unknown',
        'serial_number': 'Unknown',
        'os_version': 'Unknown',
        'uptime_days': 'Unknown',
        'memory_mb': 'Unknown'
    }
    
    version_lines = outputs.get('version', '').split('\n')
    inventory_lines = outputs.get('inventory', '').split('\n')
    
    for line in version_lines:
        line_lower = line.lower()
        
        if 'uptime is' in line_lower:
            parts = line.split()
            try:
                for i, part in enumerate(parts):
                    if 'day' in part.lower() and i > 0:
                        info['uptime_days'] = parts[i-1]
                        break
            except (IndexError, ValueError):
                pass
        
        if 'model' in line_lower or 'device id' in line_lower:
            info['model'] = line.strip()
        
        if 'version' in line_lower and 'ios' in line_lower:
            info['os_version'] = line.split('Version')[1].strip() if 'Version' in line else line.strip()
        
        if 'memory:' in line_lower or 'total memory' in line_lower:
            parts = line.split()
            for i, part in enumerate(parts):
                if 'k' in part.lower() or 'm' in part.lower() or 'g' in part.lower():
                    try:
                        info['memory_mb'] = part.rstrip(',')
                        break
                    except (IndexError, ValueError):
                        pass
    
    for line in inventory_lines:
        if 'sn' in line.lower() or 'serial' in line.lower():
            parts = line.split()
            if len(parts) > 1:
                info['serial_number'] = parts[-1]
                break
    
    return info


def generate_csv_report(devices_info: List[Dict[str, Any]], output_file: Path) -> None:
    """Generate CSV inventory report."""
    if not devices_info:
        logger.warning("No device information to report")
        return
    
    fieldnames = devices_info[0].keys()
    
    try:
        with open(output_file, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(devices_info)
        logger.info(f"CSV report written to {output_file}")
    except IOError as e:
        logger.error(f"Failed to write CSV report: {e}")
        raise


def generate_json_report(devices_info: List[Dict[str, Any]], output_file: Path) -> None:
    """Generate JSON inventory report."""
    try:
        with open(output_file, 'w') as jsonfile:
            json.dump(
                {
                    'generated': datetime.now().isoformat(),
                    'device_count': len(devices_info),
                    'devices': devices_info
                },
                jsonfile,
                indent=2
            )
        logger.info(f"JSON report written to {output_file}")
    except IOError as e:
        logger.error(f"Failed to write JSON report: {e}")
        raise


def generate_pretty_report(devices_info: List[Dict[str, Any]]) -> str:
    """Generate human-readable text report."""
    lines = [
        "\n" + "="*80,
        f"Device Inventory Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "="*80,
        f"\nTotal Devices: {len(devices_info)}\n"
    ]
    
    for device in devices_info:
        lines.extend([
            f"Hostname: {device.get('hostname', 'N/A')}",
            f"  Model: {device.get('model', 'N/A')}",
            f"  Serial: {device.get('serial_number', 'N/A')}",
            f"  OS Version: {device.get('os_version', 'N/A')}",
            f"  Uptime (days): {device.get('uptime_days', 'N/A')}",
            f"  Memory: {device.get('memory_mb', 'N/A')}",
            "-"*80
        ])
    
    return "\n".join(lines)


def main() -> int:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description='Collect system information from network devices'
    )
    parser.add_argument(
        '--output',
        default='device_inventory.csv',
        help='Output file path (default: device_inventory.csv)'
    )
    parser.add_argument(
        '--format',
        choices=['csv', 'json', 'pretty'],
        default='csv',
        help='Output format (default: csv)'
    )
    parser.add_argument(
        '--devices',
        help='Filter devices by name pattern (regex)'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Nornir config file (default: config.yaml)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file=args.config)
        
        if args.devices:
            nr = nr.filter(name__regex=args.devices)
        
        if len(nr.inventory.hosts) == 0:
            logger.error("No devices found matching filter criteria")
            return 1
        
        logger.info(f"Collecting system info from {len(nr.inventory.hosts)} devices")
        
        results = nr.run(task=gather_system_info)
        
        devices_info = []
        for host_name, multi_result in results.items():
            if not multi_result.failed:
                parsed = parse_system_info(host_name, multi_result[0].result)
                devices_info.append(parsed)
            else:
                logger.warning(f"Failed to collect info from {host_name}")
        
        output_path = Path(args.output)
        
        if args.format == 'csv':
            generate_csv_report(devices_info, output_path)
        elif args.format == 'json':
            generate_json_report(devices_info, output_path)
        elif args.format == 'pretty':
            report = generate_pretty_report(devices_info)
            print(report)
            if args.output != 'device_inventory.csv':
                with open(output_path, 'w') as f:
                    f.write(report)
                logger.info(f"Report written to {output_path}")
        
        logger.info(f"Successfully collected info from {len(devices_info)} devices")
        return 0
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
```