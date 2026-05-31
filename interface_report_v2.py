```python
"""
LLDP Neighbor Discovery and Topology Report.

Purpose: Discover network topology by collecting LLDP neighbor information from
devices and generating a comprehensive neighbor relationship report.

Usage:
    python lldp_topology_report.py --inventory inventory.yaml --group routers
    python lldp_topology_report.py --inventory inventory.yaml --device router1
    python lldp_topology_report.py --inventory inventory.yaml --format json

Prerequisites:
    - Nornir installed with NAPALM plugin
    - YAML inventory file with device configurations
    - Network connectivity to target devices
    - LLDP enabled on network devices
    - Appropriate credentials (in inventory or environment variables)

Output Formats:
    - table (default): Human-readable ASCII table of neighbors
    - json: JSON format for programmatic consumption
    - text: Detailed text report with connection summary
"""

import argparse
import json
import logging
import sys
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task
from nornir_napalm.plugins.tasks import napalm_get


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the script."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def get_lldp_neighbors(task: Task) -> Dict[str, Any]:
    """Retrieve LLDP neighbor information from device."""
    try:
        result = task.run(napalm_get, getters=["get_lldp_neighbors"])
        neighbors = result[0].result.get("get_lldp_neighbors", {})
        
        return {
            "device": task.host.name,
            "neighbors": neighbors,
            "status": "success",
        }
    except Exception as e:
        logging.warning(f"Failed to get LLDP neighbors from {task.host.name}: {e}")
        return {
            "device": task.host.name,
            "neighbors": {},
            "status": "failed",
            "error": str(e),
        }


def build_topology(results: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build topology from collected LLDP data."""
    connections = []
    
    for host, task_results in results.items():
        result = task_results[0].result
        if result.get("status") == "success":
            neighbors = result.get("neighbors", {})
            for local_intf, neighbor_list in neighbors.items():
                for neighbor in neighbor_list:
                    connections.append({
                        "source_device": result["device"],
                        "source_interface": local_intf,
                        "neighbor_device": neighbor.get("hostname", "unknown"),
                        "neighbor_interface": neighbor.get("port", "unknown"),
                    })
    
    return connections


def format_table_output(connections: List[Dict[str, str]]) -> str:
    """Format connections as ASCII table."""
    if not connections:
        return "No LLDP neighbors discovered."
    
    header = f"{'Source Device':<20} {'Source Interface':<18} {'Neighbor Device':<20} {'Neighbor Interface':<18}"
    separator = "-" * (20 + 18 + 20 + 18 + 3)
    
    rows = [header, separator]
    for conn in connections:
        row = (
            f"{conn['source_device']:<20} {conn['source_interface']:<18} "
            f"{conn['neighbor_device']:<20} {conn['neighbor_interface']:<18}"
        )
        rows.append(row)
    
    return "\n".join(rows)


def format_text_output(
    connections: List[Dict[str, str]],
    results: Dict[str, Any]
) -> str:
    """Format connections as detailed text report."""
    lines = ["LLDP Topology Report\n", "=" * 80]
    
    successful = sum(1 for r in results.values() if r[0].result.get("status") == "success")
    failed = sum(1 for r in results.values() if r[0].result.get("status") != "success")
    
    lines.append(f"\nDevices Scanned: {len(results)}")
    lines.append(f"Successful: {successful}")
    lines.append(f"Failed: {failed}")
    lines.append(f"Total Connections: {len(connections)}\n")
    
    if connections:
        lines.append("Device Connections:")
        lines.append("-" * 80)
        
        devices = {}
        for conn in connections:
            src = conn["source_device"]
            if src not in devices:
                devices[src] = []
            devices[src].append(
                f"  {conn['source_interface']} -> "
                f"{conn['neighbor_device']}:{conn['neighbor_interface']}"
            )
        
        for device, conns in sorted(devices.items()):
            lines.append(f"\n{device}:")
            lines.extend(conns)
    else:
        lines.append("No LLDP neighbors discovered on any device.")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Discover network topology using LLDP neighbor information.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "--group",
        help="Filter devices by inventory group",
    )
    parser.add_argument(
        "--device",
        help="Specific device hostname to check",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "text"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(name=args.device)
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        
        if not nr.inventory.hosts:
            logging.error("No devices matched the filter criteria.")
            sys.exit(1)
        
        logging.info(f"Collecting LLDP data from {len(nr.inventory.hosts)} device(s)...")
        results = nr.run(task=get_lldp_neighbors)
        
        connections = build_topology(results)
        
        if args.format == "json":
            output = {
                "total_devices": len(results),
                "total_connections": len(connections),
                "connections": connections,
            }
            print(json.dumps(output, indent=2))
        elif args.format == "text":
            print(format_text_output(connections, results))
        else:  # table format
            print(format_table_output(connections))
        
        logging.info("Report generated successfully.")
        
    except FileNotFoundError as e:
        logging.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```