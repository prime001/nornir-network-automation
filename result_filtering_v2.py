```python
#!/usr/bin/env python3
"""
Device Specifications Collector.

Gathers device facts and specifications using Nornir and NAPALM,
exports to CSV/JSON for documentation and inventory tracking.

Usage:
    python device_specs.py --inventory hosts.yaml --output devices.csv
    python device_specs.py --format json
    python device_specs.py --device "core*" --format csv

Prerequisites:
    - Nornir installation
    - NAPALM plugin and supported device drivers
    - Valid inventory with connection details
"""

import argparse
import csv
import json
import logging
import sys
from typing import Dict, Any, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_device_specs(task) -> Dict[str, Any]:
    """Retrieve device specifications via NAPALM."""
    specs = {"device_name": task.host.name}
    
    try:
        result = task.run(napalm_get, getters=["get_facts"])
        facts = result[task.host.name]["get_facts"]
        
        specs.update({
            "vendor": facts.get("vendor", ""),
            "model": facts.get("model", ""),
            "os_version": facts.get("os_version", ""),
            "serial": facts.get("serial_number", ""),
            "hostname": facts.get("hostname", ""),
            "uptime_days": facts.get("uptime", 0) // 86400,
            "status": "success"
        })
    except Exception as e:
        logger.error(f"{task.host.name}: {e}")
        specs["status"] = f"failed: {str(e)[:40]}"
    
    return specs


def export_csv(specs_list: List[Dict], filename: str) -> None:
    """Export specs to CSV file."""
    if not specs_list:
        logger.error("No data to export.")
        return
    
    keys = set()
    for spec in specs_list:
        keys.update(spec.keys())
    keys = sorted(list(keys))
    
    try:
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(specs_list)
        logger.info(f"Exported {len(specs_list)} devices to {filename}")
    except IOError as e:
        logger.error(f"Failed to write CSV: {e}")
        sys.exit(1)


def export_json(specs_list: List[Dict], filename: str) -> None:
    """Export specs to JSON file."""
    try:
        with open(filename, "w") as f:
            json.dump(specs_list, f, indent=2)
        logger.info(f"Exported {len(specs_list)} devices to {filename}")
    except IOError as e:
        logger.error(f"Failed to write JSON: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-i", "--inventory",
        default="inventory.yaml",
        help="Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "-o", "--output",
        default="devices.csv",
        help="Output filename (default: devices.csv)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)"
    )
    parser.add_argument(
        "--device",
        help="Filter devices by name pattern (regex)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(F(name__regex=args.device))
        
        if not nr.inventory.hosts:
            logger.error("No devices found.")
            sys.exit(1)
        
        logger.info(f"Querying {len(nr.inventory.hosts)} device(s)...")
        results = nr.run(task=get_device_specs)
        
        specs_list = []
        for device_name in results:
            task_result = results[device_name][0]
            if task_result.result:
                specs_list.append(task_result.result)
        
        if not specs_list:
            logger.error("No results gathered.")
            sys.exit(1)
        
        if args.format == "json":
            export_json(specs_list, args.output)
        else:
            export_csv(specs_list, args.output)
        
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```