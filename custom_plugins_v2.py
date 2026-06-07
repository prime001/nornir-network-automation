```python
#!/usr/bin/env python3
"""
Device Software Version Reporter

Retrieves and reports software/OS versions across network devices.
Uses NAPALM to gather device facts and generates compliance reports.

Usage:
    python device_software_versions.py -i inventory.yaml
    python device_software_versions.py -i inventory.yaml -d router1
    python device_software_versions.py -i inventory.yaml -t 15.3.1 -o json

Prerequisites:
    - Nornir configured with device inventory
    - NAPALM plugin installed (pip install nornir-napalm)
    - Network connectivity and SSH credentials to devices
    - nornir_config.yaml in working directory
"""

import argparse
import json
import logging
from pathlib import Path

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get


logger = logging.getLogger(__name__)


def get_device_software(task: Task) -> Result:
    """Retrieve device software version using NAPALM get_facts."""
    result = task.run(napalm_get, getters=["facts"])
    facts = result[0].result.get("facts", {})
    
    return Result(
        host=task.host,
        result={
            "model": facts.get("model"),
            "os_version": facts.get("os_version"),
            "serial_number": facts.get("serial_number"),
            "uptime_seconds": facts.get("uptime_seconds"),
        },
    )


def generate_text_report(device_data, target_version=None):
    """Generate human-readable report."""
    print("\n" + "=" * 80)
    print("DEVICE SOFTWARE VERSION REPORT")
    print("=" * 80 + "\n")
    
    compliant = non_compliant = 0
    
    for host in sorted(device_data.keys()):
        info = device_data[host]
        if "error" in info:
            print(f"{host:<20} [ERROR] {info['error']}\n")
            continue
        
        os_version = info.get("os_version", "Unknown")
        status = ""
        
        if target_version:
            is_compliant = os_version == target_version
            status = " [✓]" if is_compliant else " [✗]"
            compliant += is_compliant
            non_compliant += not is_compliant
        
        print(f"Host:           {host}{status}")
        print(f"  Model:        {info.get('model', 'N/A')}")
        print(f"  OS Version:   {os_version}")
        print(f"  Serial:       {info.get('serial_number', 'N/A')}")
        print(f"  Uptime (sec): {info.get('uptime_seconds', 'N/A')}\n")
    
    if target_version:
        print("=" * 80)
        print(f"Compliance Summary (Target: {target_version})")
        print(f"  Compliant:     {compliant}")
        print(f"  Non-compliant: {non_compliant}")
        print("=" * 80 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Report device software versions across network devices"
    )
    parser.add_argument(
        "-i", "--inventory",
        type=Path, default=Path("inventory.yaml"),
        help="Path to Nornir inventory file"
    )
    parser.add_argument(
        "-d", "--device",
        help="Target specific device by hostname"
    )
    parser.add_argument(
        "-g", "--group",
        help="Filter devices by group"
    )
    parser.add_argument(
        "-t", "--target-version",
        help="Target version for compliance check"
    )
    parser.add_argument(
        "-o", "--output",
        choices=["text", "json"], default="text",
        help="Output format"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    try:
        nr = InitNornir(config_file="nornir_config.yaml")
        
        if args.group:
            nr = nr.filter(group=args.group)
        if args.device:
            nr = nr.filter(name=args.device)
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter criteria")
            return
        
        logger.info(f"Scanning {len(nr.inventory.hosts)} device(s)...")
        
        device_data = {}
        for host_name in sorted(nr.inventory.hosts.keys()):
            try:
                result = get_device_software(nr[host_name])
                device_data[host_name] = result.result
            except Exception as e:
                device_data[host_name] = {"error": str(e)}
                logger.warning(f"Failed to query {host_name}: {e}")
        
        if args.output == "json":
            print(json.dumps(device_data, indent=2, default=str))
        else:
            generate_text_report(device_data, args.target_version)
            
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return


if __name__ == "__main__":
    main()
```