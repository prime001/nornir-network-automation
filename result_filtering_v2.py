```python
"""
Device Inventory Audit

Purpose:
    Collect and report device inventory information including hostname, model,
    serial number, and software version. Useful for asset management and
    compliance auditing.

Usage:
    python device_inventory_audit.py
    python device_inventory_audit.py --group core --output json
    python device_inventory_audit.py --devices r1,r2,r3 --output csv

Prerequisites:
    - Nornir inventory configured with device credentials
    - Devices must support 'show version' command
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_inventory(task: Task) -> Result:
    """Collect device model, serial number, and version information."""
    inventory_data = {
        "hostname": task.host.name,
        "timestamp": datetime.now().isoformat(),
        "reachable": False
    }
    
    try:
        version_result = task.run(
            netmiko_send_command,
            command_string="show version"
        )
        inventory_data["version_output"] = version_result.result
        inventory_data["reachable"] = True
    except Exception as e:
        logger.warning(f"Failed to gather inventory for {task.host.name}: {e}")
        inventory_data["error"] = str(e)
    
    return Result(host=task.host, result=inventory_data)


def parse_device_info(version_output: str) -> dict:
    """Extract model, serial, and version from show version output."""
    info = {"model": "Unknown", "serial": "Unknown", "version": "Unknown"}
    
    lines = version_output.split('\n')
    for i, line in enumerate(lines):
        if 'Serial Number' in line or 'serial number' in line.lower():
            info["serial"] = line.split(':')[-1].strip()
        if 'Model Number' in line or 'model number' in line.lower():
            info["model"] = line.split(':')[-1].strip()
        if any(x in line for x in ['Software Version', 'Version', 'IOS']):
            parts = line.split()
            for j, part in enumerate(parts):
                if part[0].isdigit():
                    info["version"] = part
                    break
    
    return info


def format_output(results: dict, output_format: str = "text"):
    """Format and display inventory audit results."""
    inventory = []
    
    for host, task_results in results.items():
        result = task_results[0].result
        if result["reachable"]:
            parsed = parse_device_info(result.get("version_output", ""))
            inventory.append({
                "hostname": host,
                "model": parsed["model"],
                "serial": parsed["serial"],
                "version": parsed["version"],
                "status": "reachable"
            })
        else:
            inventory.append({
                "hostname": host,
                "model": "UNREACHABLE",
                "serial": "N/A",
                "version": result.get("error", "Unknown"),
                "status": "unreachable"
            })
    
    if output_format == "json":
        print(json.dumps(inventory, indent=2))
    elif output_format == "csv":
        if inventory:
            writer = csv.DictWriter(sys.stdout, fieldnames=inventory[0].keys())
            writer.writeheader()
            writer.writerows(inventory)
    else:
        print("\n" + "=" * 90)
        print("DEVICE INVENTORY AUDIT")
        print("=" * 90)
        print(f"{'Hostname':<20} {'Model':<20} {'Serial':<20} {'Version':<15} {'Status':<12}")
        print("-" * 90)
        for item in inventory:
            print(f"{item['hostname']:<20} {item['model']:<20} {item['serial']:<20} "
                  f"{item['version']:<15} {item['status']:<12}")
        print("-" * 90)
        reachable = sum(1 for item in inventory if item["status"] == "reachable")
        print(f"Total devices: {len(inventory)} | Reachable: {reachable} | "
              f"Unreachable: {len(inventory) - reachable}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Audit and report on device inventory information"
    )
    parser.add_argument(
        "--config",
        default="nornir_config.yaml",
        help="Path to Nornir configuration file (default: nornir_config.yaml)"
    )
    parser.add_argument(
        "--devices",
        help="Comma-separated list of device hostnames to audit"
    )
    parser.add_argument(
        "--group",
        help="Audit devices in a specific group"
    )
    parser.add_argument(
        "--output",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--loglevel",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level"
    )
    
    args = parser.parse_args()
    logger.setLevel(getattr(logging, args.loglevel))
    
    try:
        nr = InitNornir(config_file=args.config)
    except FileNotFoundError:
        logger.error(f"Configuration file '{args.config}' not found")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        sys.exit(1)
    
    if args.devices:
        device_list = [d.strip() for d in args.devices.split(',')]
        nr = nr.filter(F(name__in=device_list))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))
    
    if not nr.inventory.hosts:
        logger.error("No devices found matching filter criteria")
        sys.exit(1)
    
    logger.info(f"Starting inventory audit for {len(nr.inventory.hosts)} device(s)")
    results = nr.run(task=gather_inventory)
    
    format_output(dict(results), args.output)
    logger.info("Inventory audit completed")


if __name__ == "__main__":
    main()
```