Interface Status Report Generator

Collects and reports on network interface status, statistics, and configuration
from devices managed by Nornir. Generates a formatted report of interface states,
IP assignments, and operational status across the network inventory.

Usage:
    python 003_interface_report.py --devices "router1,router2" --format table
    python 003_interface_report.py --devices all --format csv > interfaces.csv

Prerequisites:
    - Nornir inventory configured (config.yaml, hosts.yml, groups.yml)
    - Device credentials in .env or environment variables
    - netmiko or paramiko drivers installed
    - Network connectivity to all target devices
"""

import argparse
import json
import logging
import sys
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def collect_interface_status(task) -> Dict:
    """Retrieve interface status from device using netmiko."""
    try:
        device_os = task.host.platform or "ios"
        
        if device_os in ("ios", "iosxe", "iosxr"):
            cmd = "show interface brief"
        elif device_os == "nxos":
            cmd = "show interface brief"
        elif device_os == "junos":
            cmd = "show interfaces brief"
        else:
            cmd = "show interface"
        
        result = task.run(
            netmiko_send_command,
            command_string=cmd,
            use_textfsm=False
        )
        
        return {
            "device": task.host.name,
            "status": "success",
            "output": result[task.host.name][0].result
        }
    
    except Exception as e:
        logger.error(f"Failed to collect from {task.host.name}: {e}")
        return {
            "device": task.host.name,
            "status": "failed",
            "error": str(e)
        }


def parse_interface_output(raw_output: str) -> List[Dict]:
    """Parse interface output into structured interface records."""
    interfaces = []
    
    for line in raw_output.split("\n"):
        line = line.strip()
        
        if not line or "Interface" in line or "---" in line:
            continue
        
        parts = line.split()
        if len(parts) < 2:
            continue
        
        interface_record = {
            "name": parts[0],
            "ip_address": parts[1] if len(parts) > 1 else "unassigned",
            "status": parts[2] if len(parts) > 2 else "unknown",
            "protocol": parts[3] if len(parts) > 3 else "unknown",
            "line_protocol": parts[4] if len(parts) > 4 else "unknown"
        }
        interfaces.append(interface_record)
    
    return interfaces


def format_table_output(all_data: Dict[str, List[Dict]]) -> str:
    """Format results as ASCII table."""
    lines = []
    lines.append(
        f"{'Device':<18} {'Interface':<12} {'IP Address':<18} "
        f"{'Status':<10} {'Protocol':<10}"
    )
    lines.append("-" * 70)
    
    for device in sorted(all_data.keys()):
        data = all_data[device]
        
        if isinstance(data, dict) and data.get("status") == "failed":
            lines.append(f"{device:<18} {'ERROR':<12} {data.get('error', 'Unknown'):<45}")
        else:
            for intf in data:
                lines.append(
                    f"{device:<18} {intf['name']:<12} {intf['ip_address']:<18} "
                    f"{intf['status']:<10} {intf['protocol']:<10}"
                )
    
    return "\n".join(lines)


def format_json_output(all_data: Dict) -> str:
    """Format results as JSON."""
    return json.dumps(all_data, indent=2)


def format_csv_output(all_data: Dict[str, List[Dict]]) -> str:
    """Format results as CSV."""
    lines = ["Device,Interface,IP Address,Status,Protocol"]
    
    for device in sorted(all_data.keys()):
        data = all_data[device]
        
        if isinstance(data, dict) and data.get("status") == "failed":
            continue
        
        for intf in data:
            lines.append(
                f"{device},{intf['name']},{intf['ip_address']},"
                f"{intf['status']},{intf['protocol']}"
            )
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate network interface status report from Nornir inventory"
    )
    parser.add_argument(
        "--devices",
        help="Target devices: 'all' or comma-separated list",
        default="all"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format"
    )
    parser.add_argument(
        "--config",
        help="Nornir config file path",
        default="config.yaml"
    )
    
    args = parser.parse_args()
    
    try:
        logger.info(f"Initializing Nornir from {args.config}")
        nr = InitNornir(config_file=args.config)
        
        if args.devices != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
            logger.info(f"Targeting {len(nr.inventory.hosts)} specified devices")
        else:
            logger.info(f"Targeting all {len(nr.inventory.hosts)} devices in inventory")
        
        logger.info("Collecting interface data")
        results = nr.run(task=collect_interface_status)
        
        all_interfaces = {}
        for device_name in results.keys():
            task_result = results[device_name][0].result
            
            if task_result["status"] == "success":
                interfaces = parse_interface_output(task_result["output"])
                all_interfaces[device_name] = interfaces
            else:
                all_interfaces[device_name] = task_result
        
        if args.format == "table":
            output = format_table_output(all_interfaces)
        elif args.format == "json":
            output = format_json_output(all_interfaces)
        else:
            output = format_csv_output(all_interfaces)
        
        print(output)
        logger.info("Interface report completed successfully")
        
    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()