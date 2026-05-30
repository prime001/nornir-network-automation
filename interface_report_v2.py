```python
#!/usr/bin/env python3
"""
Device Fact Collection and Inventory Tool

Purpose:
    Collects device facts (model, serial number, OS version, interface count)
    from network devices and generates comprehensive inventory reports.

Usage:
    python device_facts.py --inventory inventory.yaml
    python device_facts.py --inventory inventory.yaml --devices router1,router2
    python device_facts.py --inventory inventory.yaml --output csv
    python device_facts.py --inventory inventory.yaml --filter role:router

Prerequisites:
    - Nornir and NAPALM installed (pip install nornir napalm)
    - Inventory file with device definitions in YAML format
    - Device credentials configured in inventory or environment variables
    - NAPALM drivers available for target device types
"""

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.napalm_plugins import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_device_facts(task: Task) -> Result:
    """
    Collect device facts using NAPALM get_facts method.
    
    Retrieves: hostname, model, serial number, OS version, uptime, interface count.
    """
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result.get("facts", {})
        
        return Result(
            host=task.host,
            result={
                "hostname": facts.get("hostname", "N/A"),
                "model": facts.get("model", "N/A"),
                "serial_number": facts.get("serial_number", "N/A"),
                "os_version": facts.get("os_version", "N/A"),
                "uptime_seconds": facts.get("uptime", 0),
                "interface_count": facts.get("interface_count", 0),
                "fqdn": facts.get("fqdn", "N/A"),
                "vendor": facts.get("vendor", "N/A"),
            }
        )
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to collect facts - {e}")
        return Result(host=task.host, result=None, failed=True)


def format_text_output(facts_dict: Dict[str, Any]) -> str:
    """Format inventory as readable text table."""
    lines = ["\n" + "="*130]
    lines.append("NETWORK DEVICE INVENTORY REPORT".center(130))
    lines.append("="*130)
    lines.append("")
    
    header = (
        f"{'Hostname':<20} {'Model':<25} {'Serial':<20} "
        f"{'OS Version':<20} {'Uptime (days)':<15} {'Interfaces':<12}"
    )
    lines.append(header)
    lines.append("-"*130)
    
    for hostname in sorted(facts_dict.keys()):
        facts = facts_dict[hostname]
        if facts is None:
            lines.append(f"{hostname:<20} {'UNREACHABLE':<25}")
            continue
        
        uptime_days = facts.get("uptime_seconds", 0) / 86400
        line = (
            f"{facts.get('hostname', hostname):<20} "
            f"{facts.get('model', 'N/A'):<25} "
            f"{facts.get('serial_number', 'N/A'):<20} "
            f"{facts.get('os_version', 'N/A'):<20} "
            f"{uptime_days:>14.1f} "
            f"{facts.get('interface_count', 'N/A'):>11}"
        )
        lines.append(line)
    
    lines.append("-"*130)
    lines.append("")
    return "\n".join(lines)


def format_csv_output(facts_dict: Dict[str, Any], filepath: str) -> None:
    """Export inventory as CSV file."""
    with open(filepath, 'w', newline='') as f:
        fieldnames = [
            'device_name', 'hostname', 'vendor', 'model', 'serial_number',
            'os_version', 'uptime_days', 'interface_count', 'fqdn'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for device_name in sorted(facts_dict.keys()):
            facts = facts_dict[device_name]
            if facts is None:
                writer.writerow({'device_name': device_name, 'hostname': 'UNREACHABLE'})
                continue
            
            writer.writerow({
                'device_name': device_name,
                'hostname': facts.get('hostname', 'N/A'),
                'vendor': facts.get('vendor', 'N/A'),
                'model': facts.get('model', 'N/A'),
                'serial_number': facts.get('serial_number', 'N/A'),
                'os_version': facts.get('os_version', 'N/A'),
                'uptime_days': facts.get('uptime_seconds', 0) / 86400,
                'interface_count': facts.get('interface_count', 0),
                'fqdn': facts.get('fqdn', 'N/A'),
            })
    
    logger.info(f"CSV inventory exported to {filepath}")


def run_collection(nr, devices: list = None):
    """Execute fact collection across inventory."""
    if devices:
        nr = nr.filter(name__in=devices)
    
    logger.info(f"Collecting facts from {len(nr.inventory.hosts)} devices")
    results = nr.run(task=collect_device_facts)
    
    facts_dict = {}
    for hostname, task_result in results.items():
        if task_result[0].failed:
            facts_dict[hostname] = None
        else:
            facts_dict[hostname] = task_result[0].result
    
    return facts_dict


def main():
    parser = argparse.ArgumentParser(
        description="Collect network device facts and generate inventory reports",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "-d", "--devices",
        help="Comma-separated device names to query"
    )
    parser.add_argument(
        "-o", "--output",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--output-file",
        help="Save output to file"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)
        
        devices = None
        if args.devices:
            devices = [d.strip() for d in args.devices.split(",")]
        
        facts_dict = run_collection(nr, devices)
        
        if args.output == "json":
            output = json.dumps(facts_dict, indent=2, default=str)
            if args.output_file:
                Path(args.output_file).write_text(output)
                logger.info(f"JSON inventory saved to {args.output_file}")
            else:
                print(output)
        
        elif args.output == "csv":
            filepath = args.output_file or "inventory.csv"
            format_csv_output(facts_dict, filepath)
        
        else:
            output = format_text_output(facts_dict)
            if args.output_file:
                Path(args.output_file).write_text(output)
                logger.info(f"Text inventory saved to {args.output_file}")
            else:
                print(output)
        
        logger.info("Inventory collection completed successfully")
        return 0
    
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```