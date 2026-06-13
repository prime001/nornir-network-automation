```python
"""
Network Topology Discovery using LLDP/CDP.

Purpose:
    Discovers network device neighbors using LLDP or CDP protocol and generates
    a topology map showing device interconnections. Useful for understanding
    network structure and identifying potential connectivity issues.

Usage:
    python topology_discovery.py --inventory inventory.yaml
    python topology_discovery.py --devices "core|dist" --output topology.json

Prerequisites:
    - Nornir installed: pip install nornir nornir-netmiko napalm
    - Devices accessible via SSH
    - LLDP or CDP enabled on network devices
"""

import argparse
import json
import logging
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def discover_neighbors(task: Task) -> Result:
    """
    Discover LLDP neighbors for a device.
    
    Args:
        task: Nornir task object
    
    Returns:
        Result object containing neighbor information
    """
    try:
        lldp_result = task.run(napalm_get, getters=["lldp_neighbors"])
        neighbors = lldp_result[0].result.get("lldp_neighbors", {})
        
        parsed_neighbors = {}
        for local_port, remote_devices in neighbors.items():
            for remote_device in remote_devices:
                parsed_neighbors[local_port] = {
                    "remote_device": remote_device.get("hostname", "unknown"),
                    "remote_port": remote_device.get("port", "unknown"),
                }
        
        return Result(host=task.host, result=parsed_neighbors)
    
    except Exception as e:
        logger.warning(f"LLDP discovery failed on {task.host}: {e}")
        return Result(host=task.host, result={})


def build_topology_map(results: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """Build network topology from discovered neighbors."""
    topology = {}
    
    for host, result in results.items():
        if result[0].result:
            topology[host] = []
            for local_port, neighbor_info in result[0].result.items():
                topology[host].append({
                    "local_port": local_port,
                    "remote_host": neighbor_info["remote_device"],
                    "remote_port": neighbor_info["remote_port"]
                })
    
    return topology


def print_topology(topology: Dict[str, List[Dict[str, str]]]) -> None:
    """Print formatted topology report."""
    print("\n" + "=" * 80)
    print("NETWORK TOPOLOGY MAP")
    print("=" * 80 + "\n")
    
    if not topology:
        print("No neighbor relationships discovered\n")
        return
    
    for device in sorted(topology.keys()):
        neighbors = topology[device]
        if neighbors:
            print(f"{device}:")
            for link in neighbors:
                print(f"  {link['local_port']:12} → "
                      f"{link['remote_host']:20} {link['remote_port']}")
        else:
            print(f"{device}: (no LLDP neighbors)")
        print()
    
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "--devices",
        help="Regex filter for device hostnames (e.g., 'core|dist')"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file for topology data"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.devices:
            nr = nr.filter(name__regex=args.devices)
        
        if not nr.inventory.hosts:
            logger.warning("No devices matched the filter criteria")
            return
        
        logger.info(f"Discovering neighbors from {len(nr.inventory.hosts)} "
                    f"device(s)")
        
        results = nr.run(task=discover_neighbors)
        topology = build_topology_map(dict(results))
        
        print_topology(topology)
        
        if args.output:
            with open(args.output, "w") as f:
                json.dump(topology, f, indent=2)
            logger.info(f"Topology exported to {args.output}")
        
        successful = sum(1 for r in results.values() if r[0].result)
        logger.info(f"Successfully discovered neighbors from {successful} "
                    f"device(s)")
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```