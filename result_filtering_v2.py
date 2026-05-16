```python
#!/usr/bin/env python3
"""
Software Version Inventory Aggregator

Collects OS versions from all network devices and generates a report
showing version distribution and outdated systems. Filters results by
version status (current, outdated, critical).

Prerequisites:
    - Nornir installed with netmiko driver
    - Network devices accessible via SSH
    - Devices support 'show version' command
    - Inventory configured with device_type and credentials

Usage:
    python software_inventory.py --inventory inventory.yaml
    python software_inventory.py --inventory inventory.yaml --outdated-only
    python software_inventory.py --inventory inventory.yaml --filter "core-*"
    python software_inventory.py --inventory inventory.yaml --group-by-version --verbose
"""

import argparse
import logging
from collections import defaultdict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.tasks.networking import netmiko_send_command

logger = logging.getLogger(__name__)

VERSION_THRESHOLD = {
    "cisco_ios": {"current": "16.12", "outdated": "15.0"},
    "cisco_xr": {"current": "7.0", "outdated": "6.0"},
    "juniper_junos": {"current": "20.1", "outdated": "19.0"},
    "arista_eos": {"current": "4.28", "outdated": "4.20"},
}


def extract_version(output: str, device_type: str) -> str:
    """Parse version string from device output based on device type."""
    for line in output.split("\n"):
        if any(x in line for x in ["Software Version", "Cisco IOS", "Junos:", "Software version"]):
            tokens = line.split()
            for token in tokens:
                if token[0].isdigit() and "." in token and len(token) < 10:
                    return token
    return "Unknown"


def get_device_version(task: Task) -> Result:
    """Collect version from device via netmiko."""
    device_type = task.host.get("device_type", "cisco_ios")
    
    try:
        r = task.run(netmiko_send_command, command_string="show version")
        version = extract_version(r[0].result, device_type)
        
        return Result(host=task.host, result={
            "version": version,
            "device_type": device_type,
        })
    except Exception as e:
        logger.error(f"Failed to get version from {task.host.name}: {e}")
        return Result(host=task.host, failed=True, result={"version": "Error"})


def classify_version(version: str, device_type: str) -> str:
    """Classify version status."""
    if version == "Unknown" or version == "Error":
        return "unknown"
    
    threshold = VERSION_THRESHOLD.get(device_type, {})
    try:
        curr = tuple(map(int, threshold.get("current", "0.0").split(".")[:2]))
        outd = tuple(map(int, threshold.get("outdated", "0.0").split(".")[:2]))
        vers = tuple(map(int, version.split(".")[:2]))
        
        if vers >= curr:
            return "current"
        elif vers >= outd:
            return "outdated"
        else:
            return "critical"
    except (ValueError, AttributeError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory", default="inventory.yaml", help="Inventory file")
    parser.add_argument("--filter", help="Filter devices by name")
    parser.add_argument("--outdated-only", action="store_true", help="Show only outdated versions")
    parser.add_argument("--group-by-version", action="store_true", help="Group by version")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                       format="%(levelname)s: %(message)s")
    
    try:
        nr = InitNornir(config_file=args.inventory)
        if args.filter:
            nr = nr.filter(F(name__contains=args.filter))
        
        logger.info(f"Collecting versions from {len(nr.inventory.hosts)} devices...")
        
        inventory = {}
        for host_name, host_obj in nr.inventory.hosts.items():
            result = get_device_version(host_obj)
            if not result.failed:
                version = result.result["version"]
                device_type = result.result["device_type"]
                status = classify_version(version, device_type)
                inventory[host_name] = {"version": version, "device_type": device_type, "status": status}
        
        if args.group_by_version:
            grouped = defaultdict(list)
            for device, info in inventory.items():
                grouped[info["version"]].append(device)
            
            print("\nVersions by Release:")
            for version in sorted(grouped.keys()):
                devices = grouped[version]
                if args.outdated_only and classify_version(version, "cisco_ios") == "current":
                    continue
                print(f"\n{version}: {len(devices)} device(s)")
                for d in sorted(devices):
                    print(f"  - {d}")
        else:
            print(f"\n{'Device':<25} {'Type':<15} {'Version':<12} {'Status':<10}")
            print("-" * 65)
            for device in sorted(inventory.keys()):
                info = inventory[device]
                if args.outdated_only and info["status"] == "current":
                    continue
                print(f"{device:<25} {info['device_type']:<15} {info['version']:<12} {info['status']:<10}")
        
        logger.info(f"Successfully processed {len(inventory)} devices")
        return 0
    
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```