```python
"""
Interface Redundancy Checker - Nornir Network Automation Script

Purpose:
    Verify interface redundancy configurations including port-channels, LAGs,
    and failover links. Identifies single points of failure and redundancy gaps
    in network infrastructure.

Usage:
    python interface_redundancy_check.py --inventory hosts.yaml
    python interface_redundancy_check.py --device "router1" --output json
    python interface_redundancy_check.py --output-file report.json --verbose

Prerequisites:
    - nornir with napalm or netmiko
    - SSH access to network devices
    - Inventory file with device definitions
    - Credentials configured via environment or inventory
"""

import logging
import argparse
import json
import sys
from typing import Dict, List
from collections import defaultdict
from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get


logger = logging.getLogger(__name__)


def check_redundancy(task):
    """
    Check interface redundancy status on device.
    Analyzes port-channels, LAGs, and single points of failure.
    """
    try:
        result = task.run(napalm_get, getters=["interfaces"])
        interfaces = result[0].result.get("interfaces", {})
        
        status = {
            "device": task.host.name,
            "port_channels": [],
            "eth_uplinks": [],
            "single_points_of_failure": [],
        }
        
        # Identify and check port-channels/LAGs
        for iface_name, iface_data in interfaces.items():
            name_lower = iface_name.lower()
            
            # Port-channel/LAG detection
            if any(x in name_lower for x in ["port-channel", "lag", "po"]):
                status["port_channels"].append({
                    "name": iface_name,
                    "is_up": iface_data.get("is_up", False),
                    "mtu": iface_data.get("mtu"),
                    "speed": iface_data.get("speed"),
                })
            
            # Uplink detection
            elif any(x in name_lower for x in ["uplink", "wan", "core"]):
                status["eth_uplinks"].append({
                    "name": iface_name,
                    "is_up": iface_data.get("is_up", False),
                    "speed": iface_data.get("speed"),
                })
        
        # Flag single points of failure
        if len(status["eth_uplinks"]) == 1:
            status["single_points_of_failure"].append({
                "type": "single_uplink",
                "interface": status["eth_uplinks"][0]["name"],
                "severity": "critical",
                "recommendation": "Add redundant uplink",
            })
        
        down_port_channels = [
            pc for pc in status["port_channels"] if not pc["is_up"]
        ]
        if down_port_channels:
            status["single_points_of_failure"].append({
                "type": "down_redundancy_link",
                "count": len(down_port_channels),
                "severity": "high",
                "recommendation": "Investigate failed port-channel member links",
            })
        
        task.host["redundancy_status"] = status
        
    except Exception as e:
        logger.warning(f"Error checking {task.host.name}: {e}")
        task.host["redundancy_status"] = {
            "device": task.host.name,
            "error": str(e),
        }


def format_text_report(results: List[Dict]) -> str:
    """Format results as human-readable text report."""
    report = "Interface Redundancy Check Report\n"
    report += "=" * 70 + "\n\n"
    
    total_devices = len(results)
    devices_with_issues = sum(1 for r in results if r.get("single_points_of_failure"))
    
    report += f"Summary:\n"
    report += f"  Total Devices: {total_devices}\n"
    report += f"  Devices with Issues: {devices_with_issues}\n\n"
    
    for result in results:
        report += f"Device: {result['device']}\n"
        report += "-" * 70 + "\n"
        
        if "error" in result:
            report += f"  Error: {result['error']}\n"
            continue
        
        port_channels = result.get("port_channels", [])
        uplinks = result.get("eth_uplinks", [])
        spofs = result.get("single_points_of_failure", [])
        
        report += f"  Port-Channels: {len(port_channels)}\n"
        for pc in port_channels:
            status_str = "UP" if pc["is_up"] else "DOWN"
            report += f"    {pc['name']:20} [{status_str}]\n"
        
        report += f"  Uplinks: {len(uplinks)}\n"
        for ul in uplinks:
            status_str = "UP" if ul["is_up"] else "DOWN"
            report += f"    {ul['name']:20} [{status_str}]\n"
        
        if spofs:
            report += f"  Issues Found: {len(spofs)}\n"
            for spof in spofs:
                report += f"    [{spof['severity'].upper()}] {spof['type']}\n"
                report += f"      → {spof['recommendation']}\n"
        else:
            report += f"  Status: No redundancy issues found\n"
        
        report += "\n"
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Check interface redundancy and identify single points of failure"
    )
    parser.add_argument(
        "--inventory",
        default="hosts.yaml",
        help="Nornir inventory file path"
    )
    parser.add_argument(
        "--device",
        help="Filter results to specific device"
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format"
    )
    parser.add_argument(
        "--output-file",
        help="Write report to file"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(F(name=args.device))
        
        if not nr.inventory.hosts:
            logger.error("No devices found matching filter criteria")
            return 1
        
        logger.info(f"Starting redundancy check on {len(nr.inventory.hosts)} device(s)")
        
        nr.run(task=check_redundancy)
        
        results = [
            host.get("redundancy_status", {})
            for host in nr.inventory.hosts.values()
        ]
        
        if args.output == "json":
            output = json.dumps(results, indent=2)
        else:
            output = format_text_report(results)
        
        print(output)
        
        if args.output_file:
            with open(args.output_file, "w") as f:
                f.write(output)
            logger.info(f"Report written to {args.output_file}")
        
        issues_count = sum(
            len(r.get("single_points_of_failure", []))
            for r in results
            if "error" not in r
        )
        logger.info(f"Redundancy check complete. Found {issues_count} issue(s)")
        
        return 0 if issues_count == 0 else 1
    
    except FileNotFoundError as e:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logger.error(f"Redundancy check failed: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```