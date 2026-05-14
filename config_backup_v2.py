```python
"""
MAC Address Table Exporter

Collects and analyzes MAC address tables from network switches to identify
devices on the network and their physical port locations.

Usage:
    python mac_table_exporter.py --devices all
    python mac_table_exporter.py --devices Switch1,Switch2 --mac 00:11:22:33:44:55
    python mac_table_exporter.py --devices all --vlan 10 --output json

Prerequisites:
    - Nornir inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
    - SSH/Netmiko access to switch devices
    - Devices must support 'show mac address-table' command

Output:
    - MAC address details with port and VLAN information
    - Device location mapping showing which switch/port each MAC is learned on
    - JSON, CSV, or table format for further analysis and integration
"""

import argparse
import csv
import json
import logging
from io import StringIO
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_mac_table(output: str, device_name: str) -> list:
    """Parse switch MAC address table output into structured format."""
    macs = []
    
    for line in output.split('\n'):
        line = line.strip()
        if not line or 'Mac Address' in line or 'VLAN' in line or '---' in line:
            continue
        
        parts = line.split()
        if len(parts) >= 4:
            try:
                mac = parts[0]
                vlan = parts[1]
                mac_type = parts[2]
                interface = parts[3] if len(parts) > 3 else 'Unknown'
                
                if ':' in mac and len(mac) == 17:
                    macs.append({
                        'mac_address': mac,
                        'vlan': vlan,
                        'type': mac_type,
                        'interface': interface,
                        'switch': device_name
                    })
            except (ValueError, IndexError):
                continue
    
    return macs


def collect_mac_tables(nr, devices_filter: str = None) -> list:
    """Collect MAC address tables from specified switches."""
    if devices_filter and devices_filter != 'all':
        device_list = [d.strip() for d in devices_filter.split(',')]
        nr_filtered = nr.filter(F(name__in=device_list))
    else:
        nr_filtered = nr
    
    all_macs = []
    
    for host_name, host in nr_filtered.inventory.hosts.items():
        try:
            result = host.run_task(
                netmiko_send_command,
                command_string="show mac address-table"
            )
            
            if result[0].failed:
                logger.warning(f"Failed to retrieve MAC table from {host_name}")
                continue
            
            macs = parse_mac_table(result[0].result, host_name)
            all_macs.extend(macs)
            logger.info(f"✓ Retrieved {len(macs)} MAC entries from {host_name}")
        
        except Exception as e:
            logger.warning(f"Error querying {host_name}: {e}")
    
    return all_macs


def filter_macs(all_macs: list, mac_filter: str = None, vlan_filter: str = None) -> list:
    """Apply filters to MAC table entries."""
    filtered = all_macs
    
    if mac_filter:
        mac_filter = mac_filter.lower()
        filtered = [m for m in filtered if mac_filter in m['mac_address'].lower()]
    
    if vlan_filter:
        filtered = [m for m in filtered if m['vlan'] == vlan_filter]
    
    return filtered


def format_table_output(macs: list) -> str:
    """Format MAC entries as human-readable table."""
    if not macs:
        return "\nNo MAC entries found matching criteria.\n"
    
    lines = []
    lines.append("\n" + "="*120)
    lines.append("MAC ADDRESS TABLE")
    lines.append("="*120)
    lines.append(f"{'MAC Address':<18} {'VLAN':<8} {'Type':<10} {'Interface':<25} {'Switch':<20}")
    lines.append("-"*120)
    
    for entry in sorted(macs, key=lambda x: (x['switch'], x['mac_address'])):
        lines.append(
            f"{entry['mac_address']:<18} {entry['vlan']:<8} {entry['type']:<10} "
            f"{entry['interface']:<25} {entry['switch']:<20}"
        )
    
    lines.append("-"*120)
    lines.append(f"Total entries: {len(macs)}")
    lines.append("="*120 + "\n")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--devices', default='all',
                       help='Device names (comma-separated) or "all"')
    parser.add_argument('--mac', help='Filter by MAC address')
    parser.add_argument('--vlan', help='Filter by VLAN ID')
    parser.add_argument('--output', choices=['table', 'json', 'csv'], default='table',
                       help='Output format')
    parser.add_argument('--inventory', default='.',
                       help='Path to nornir inventory directory')
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(
            inventory={
                "plugin": "SimpleInventory",
                "options": {
                    "host_file": str(Path(args.inventory) / "hosts.yaml"),
                    "group_file": str(Path(args.inventory) / "groups.yaml"),
                    "defaults_file": str(Path(args.inventory) / "defaults.yaml"),
                }
            }
        )
        logger.info(f"Nornir initialized with {len(nr.inventory.hosts)} hosts")
        
        all_macs = collect_mac_tables(nr, args.devices)
        filtered_macs = filter_macs(all_macs, args.mac, args.vlan)
        
        if args.output == 'json':
            print(json.dumps(filtered_macs, indent=2))
        elif args.output == 'csv':
            if filtered_macs:
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=filtered_macs[0].keys())
                writer.writeheader()
                writer.writerows(filtered_macs)
                print(output.getvalue())
            else:
                print("No entries to export")
        else:
            print(format_table_output(filtered_macs))
        
        logger.info(f"Complete: {len(filtered_macs)} entries after filtering")
        return 0
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```