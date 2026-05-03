```python
"""
Device Inventory & Version Tracking

Gathers device facts (vendor, model, OS version, serial) to track
hardware/software versions and identify mismatches across the network.
Useful for planning upgrades and compliance verification.

Prerequisites:
  - Nornir configured with valid inventory
  - Devices support NAPALM 'facts' getter
  - SSH credentials configured in Nornir

Usage:
  python script.py --config config.yaml
  python script.py --group core --output inventory.json
  python script.py --vendor cisco --verbose
"""

import logging
import argparse
import json
from datetime import datetime
from collections import defaultdict
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def gather_device_facts(task):
    """Retrieve device facts from target device."""
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result.get("facts", {})
        logger.info(f"{task.host.name}: Successfully gathered facts")
        return {
            "ip": task.host.hostname,
            "vendor": facts.get("vendor", "unknown"),
            "model": facts.get("model", "unknown"),
            "os_version": facts.get("os_version", "unknown"),
            "serial": facts.get("serial_number", "unknown"),
            "uptime": facts.get("uptime_seconds", 0),
        }
    except Exception as e:
        logger.error(f"{task.host.name}: {str(e)}")
        return {
            "ip": task.host.hostname,
            "error": str(e),
        }


def collect_inventory(nr):
    """Collect inventory data from all devices in inventory."""
    inventory = {}
    
    for device_name, device in nr.inventory.hosts.items():
        logger.debug(f"Processing {device_name}")
        from nornir.core.task import Task
        
        task = Task(name="gather_facts")
        task.host = device
        facts = gather_device_facts(task)
        
        inventory[device_name] = facts
    
    return inventory


def generate_report(inventory, vendor_filter=None):
    """Generate formatted inventory report."""
    print("\n" + "="*80)
    print("NETWORK DEVICE INVENTORY REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    by_vendor = defaultdict(list)
    by_version = defaultdict(list)
    errors = []
    
    for device, data in inventory.items():
        if "error" in data:
            errors.append((device, data["error"]))
            continue
        
        vendor = data.get("vendor", "unknown")
        if vendor_filter and vendor.lower() != vendor_filter.lower():
            continue
        
        by_vendor[vendor].append((device, data))
        by_version[data.get("os_version", "unknown")].append(device)
    
    # Device listing by vendor
    for vendor in sorted(by_vendor.keys()):
        devices = by_vendor[vendor]
        print(f"\n{vendor.upper()} ({len(devices)} devices)")
        print("-" * 80)
        
        for device, data in devices:
            print(f"  {device:20} {data['model']:20} {data['os_version']:15} "
                  f"{data['serial']}")
    
    # Version distribution
    if by_version:
        print("\n" + "="*80)
        print("OS VERSION DISTRIBUTION")
        print("="*80)
        
        for version in sorted(by_version.keys(), 
                              key=lambda v: len(by_version[v]), reverse=True):
            count = len(by_version[version])
            pct = (count / (len(inventory) - len(errors))) * 100
            print(f"  {version:30} {count:3} devices ({pct:5.1f}%)")
    
    # Error summary
    if errors:
        print("\n" + "="*80)
        print(f"ERRORS ({len(errors)} devices)")
        print("="*80)
        
        for device, error in errors:
            print(f"  {device:20} {error}")
    
    print("\n" + "="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", "-c", default="config.yaml",
                        help="Nornir config file (default: config.yaml)")
    parser.add_argument("--group", "-g", 
                        help="Filter devices by group (e.g., core, access)")
    parser.add_argument("--vendor", "-v",
                        help="Filter report by vendor (e.g., cisco, juniper)")
    parser.add_argument("--output", "-o",
                        help="Export inventory to JSON file")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable verbose logging")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    
    try:
        logger.info(f"Loading Nornir config: {args.config}")
        nr = InitNornir(config_file=args.config)
        
        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
            logger.info(f"Filtered to group '{args.group}'")
        
        logger.info(f"Collecting facts from {len(nr.inventory.hosts)} devices")
        inventory = collect_inventory(nr)
        
        generate_report(inventory, args.vendor)
        
        if args.output:
            with open(args.output, "w") as f:
                json.dump(inventory, f, indent=2)
            logger.info(f"Inventory exported to {args.output}")
        
        return 0
        
    except FileNotFoundError as e:
        logger.error(f"Config file not found: {e}")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```