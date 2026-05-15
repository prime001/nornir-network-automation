```python
"""
Network Neighbor Discovery Report using Nornir

Purpose:
    Discovers and reports on network neighbors using LLDP/CDP protocols.
    Helps identify device connections and build network topology maps.
    Useful for capacity planning, troubleshooting, and documentation.

Usage:
    python neighbor_discovery.py --inventory inventory.yaml
    python neighbor_discovery.py --inventory inventory.yaml --device router1
    python neighbor_discovery.py --inventory inventory.yaml --export neighbors.csv

Prerequisites:
    - Nornir installed with napalm support (pip install nornir napalm)
    - Network devices with SSH/Netconf access
    - LLDP/CDP enabled on network devices
    - Valid inventory.yaml with device groups, hosts, and connection details
    - Supported OS: Cisco IOS/IOS-XE/NXOS, Arista EOS, Juniper Junos, etc.
"""

import argparse
import csv
import logging
from datetime import datetime
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def discover_neighbors(task: Task) -> Result:
    """
    Retrieve neighbor information from a device using NAPALM getters.
    
    Collects LLDP neighbor data and details about connected peers.
    """
    try:
        result = task.run(
            napalm_get,
            getters=["lldp_neighbors", "lldp_neighbors_detail"]
        )
        neighbors = result[0].result.get("lldp_neighbors", {})
        details = result[0].result.get("lldp_neighbors_detail", {})
        
        return Result(
            host=task.host,
            result={
                "neighbors": neighbors,
                "details": details
            }
        )
    except Exception as e:
        logger.warning(f"{task.host}: Failed to discover neighbors - {e}")
        return Result(host=task.host, failed=True, result=str(e))


def format_report(results, export_file=None):
    """
    Format and display neighbor discovery results.
    
    Optionally exports data to CSV for integration with other tools.
    """
    print("\n" + "=" * 110)
    print(f"NETWORK NEIGHBOR DISCOVERY REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 110 + "\n")
    
    neighbor_list = []
    successful_devices = 0
    
    for host, multi_result in results.items():
        for result in multi_result:
            if result.failed:
                print(f"[FAILED] {host}: {result.result}")
                continue
            
            successful_devices += 1
            neighbors = result.result.get("neighbors", {})
            details = result.result.get("details", {})
            
            if not neighbors:
                print(f"{host}: No neighbors discovered (LLDP may be disabled)\n")
                continue
            
            print(f"Device: {host}")
            print("-" * 110)
            
            for interface, neighbor_list_intf in neighbors.items():
                for neighbor in neighbor_list_intf:
                    detail = details.get(interface, {}).get(neighbor, {})
                    
                    entry = {
                        'local_device': host,
                        'local_interface': interface,
                        'neighbor_device': neighbor,
                        'neighbor_interface': detail.get("port_description", "Unknown"),
                        'neighbor_platform': detail.get("system_description", "Unknown")
                    }
                    neighbor_list.append(entry)
                    
                    neighbor_port = entry['neighbor_interface']
                    platform = entry['neighbor_platform'][:40]
                    print(f"  {interface:20} <--> {neighbor:20} ({neighbor_port:20}) {platform}")
            print()
    
    if export_file and neighbor_list:
        with open(export_file, 'w', newline='') as csvfile:
            fieldnames = neighbor_list[0].keys()
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(neighbor_list)
        logger.info(f"Exported {len(neighbor_list)} neighbor relationships to {export_file}")
    
    print("=" * 110)
    print(f"Summary: {successful_devices} devices queried, {len(neighbor_list)} neighbors discovered")
    print("=" * 110 + "\n")
    
    return neighbor_list


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discover and report network topology using LLDP/CDP neighbor data"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "--device",
        help="Target specific device by hostname (optional)"
    )
    parser.add_argument(
        "--export",
        help="Export results to CSV file for further analysis"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(name=args.device)
            logger.info(f"Targeting specific device: {args.device}")
        
        device_count = len(nr.inventory.hosts)
        logger.info(f"Starting neighbor discovery on {device_count} device(s)")
        
        results = nr.run(task=discover_neighbors)
        format_report(results, args.export)
        
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        exit(1)
    except Exception as e:
        logger.error(f"Failed to run neighbor discovery: {e}")
        exit(1)
```