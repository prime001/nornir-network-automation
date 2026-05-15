```python
"""
Network Device Inventory Report

Collects detailed device inventory information including hardware details,
software versions, installed modules, and serial numbers. Generates a
comprehensive inventory report useful for lifecycle management and
audit compliance.

Usage:
    python device_inventory.py [--inventory INVENTORY] [--format json|csv]
    
Prerequisites:
    - Nornir with napalm plugin configured
    - Network device access (SSH)
    - Devices support get_facts() napalm method
"""

import argparse
import csv
import json
import logging
import sys
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def collect_inventory(task: Task) -> Result:
    """
    Collect device inventory information using NAPALM get_facts.
    
    Returns:
        dict: Device inventory with hostname, OS, serial, model, uptime
    """
    device_name = task.host.name
    inventory_data = {
        "device": device_name,
        "hostname": None,
        "os_version": None,
        "serial_number": None,
        "vendor": None,
        "model": None,
        "uptime_seconds": None,
        "interfaces_count": 0,
        "error": None
    }
    
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result.get("facts", {})
        
        if facts:
            inventory_data["hostname"] = facts.get("hostname", device_name)
            inventory_data["os_version"] = facts.get("os_version")
            inventory_data["serial_number"] = facts.get("serial_number", "N/A")
            inventory_data["vendor"] = facts.get("vendor")
            inventory_data["model"] = facts.get("model")
            inventory_data["uptime_seconds"] = facts.get("uptime_seconds")
            inventory_data["interfaces_count"] = facts.get("interfaces_count", 0)
        
        return Result(
            host=task.host,
            result=inventory_data,
            changed=False
        )
    
    except Exception as e:
        logger.error(f"Failed to collect inventory for {device_name}: {str(e)}")
        inventory_data["error"] = str(e)
        return Result(
            host=task.host,
            result=inventory_data,
            failed=True,
            exception=e
        )


def format_uptime_short(seconds: int) -> str:
    """Convert uptime seconds to short format."""
    if not seconds:
        return "Unknown"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    
    return f"{days}d {hours}h"


def generate_json_report(inventory_list: List[Dict[str, Any]]) -> None:
    """Output inventory as JSON."""
    output = {
        "inventory": inventory_list,
        "device_count": len(inventory_list),
        "vendors": list(set(
            item.get("vendor") for item in inventory_list if item.get("vendor")
        ))
    }
    print(json.dumps(output, indent=2))


def generate_csv_report(inventory_list: List[Dict[str, Any]]) -> None:
    """Output inventory as CSV."""
    if not inventory_list:
        logger.warning("No inventory data to report")
        return
    
    fieldnames = [
        "device", "hostname", "vendor", "model", "os_version",
        "serial_number", "uptime", "interfaces_count", "error"
    ]
    
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    
    for item in inventory_list:
        row = {
            "device": item.get("device"),
            "hostname": item.get("hostname"),
            "vendor": item.get("vendor"),
            "model": item.get("model"),
            "os_version": item.get("os_version"),
            "serial_number": item.get("serial_number"),
            "uptime": format_uptime_short(item.get("uptime_seconds", 0))
            if item.get("uptime_seconds") else "Unknown",
            "interfaces_count": item.get("interfaces_count", 0),
            "error": item.get("error", "")
        }
        writer.writerow(row)


def generate_table_report(inventory_list: List[Dict[str, Any]]) -> None:
    """Output inventory as formatted table."""
    print("\n" + "="*100)
    print("NETWORK DEVICE INVENTORY REPORT")
    print("="*100)
    print(f"{'Device':<15} {'Hostname':<15} {'Vendor':<12} {'Model':<20} "
          f"{'OS Version':<12} {'Serial':<12} {'Uptime':<12}")
    print("-"*100)
    
    for item in inventory_list:
        status = "FAIL" if item.get("error") else "OK"
        uptime = format_uptime_short(item.get("uptime_seconds", 0)) \
                 if item.get("uptime_seconds") else "Unknown"
        
        print(f"{item.get('device', 'N/A'):<15} "
              f"{(item.get('hostname', 'N/A')[:14]):<15} "
              f"{(item.get('vendor', 'N/A')[:11]):<12} "
              f"{(item.get('model', 'N/A')[:19]):<20} "
              f"{(item.get('os_version', 'N/A')[:11]):<12} "
              f"{(item.get('serial_number', 'N/A')[:11]):<12} "
              f"{uptime:<12}")
        
        if item.get("error"):
            print(f"  └─ Error: {item['error']}")
    
    print("="*100)
    print(f"Total Devices: {len(inventory_list)}")
    successful = sum(1 for item in inventory_list if not item.get("error"))
    print(f"Successful: {successful} | Failed: {len(inventory_list) - successful}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Collect network device inventory information"
    )
    parser.add_argument(
        "--inventory",
        default="inventory",
        help="Path to inventory directory (default: inventory)"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--devices",
        help="Comma-separated list of devices to include"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name__in=device_list)
        
        if not nr.inventory.hosts:
            logger.error("No devices found in inventory")
            return
        
        logger.info(f"Collecting inventory from {len(nr.inventory.hosts)} devices")
        results = nr.run(task=collect_inventory)
        
        inventory_list = [
            host_data[0].result
            for host_data in results.values()
            if host_data
        ]
        
        if args.format == "json":
            generate_json_report(inventory_list)
        elif args.format == "csv":
            generate_csv_report(inventory_list)
        else:
            generate_table_report(inventory_list)
    
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```