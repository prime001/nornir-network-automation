```python
#!/usr/bin/env python3
"""
LLDP Neighbor Discovery and Network Topology Report

Purpose:
    Discovers network topology by collecting and reporting LLDP (Link Layer
    Discovery Protocol) neighbor information from network devices. Useful for
    mapping physical network interconnections and validating cabling.

Usage:
    python lldp_neighbors.py -i inventory -u admin -p password
    python lldp_neighbors.py -i inventory -u admin -p password --format json
    python lldp_neighbors.py -i inventory -u admin -p password --filter site:west

Prerequisites:
    - Nornir and NAPALM installed: pip install nornir napalm
    - Devices must have LLDP enabled and configured
    - Valid SSH/Netconf credentials required for device access
    - Supported platforms: Cisco IOS/XE/XR, Juniper, Arista, and other NAPALM drivers
"""

import argparse
import json
import logging
import sys
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get


logger = logging.getLogger(__name__)


def collect_lldp_neighbors(task) -> Dict[str, Any]:
    """Gather LLDP neighbor data from device using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["lldp_neighbors"])
        neighbors = result[0].result.get("lldp_neighbors", {})
        logger.debug(f"{task.host.name}: collected {len(neighbors)} interfaces with neighbors")
        return {"status": "success", "data": neighbors}
    except Exception as e:
        logger.error(f"{task.host.name}: {e}")
        return {"status": "failed", "error": str(e)}


def print_text_report(results: Dict) -> None:
    """Print human-readable topology report."""
    print("\n" + "=" * 70)
    print("LLDP Network Topology Report".center(70))
    print("=" * 70)
    
    for host_name in sorted(results.keys()):
        host_result = results[host_name][0]
        
        if not host_result.result or host_result.result["status"] != "success":
            error = host_result.result.get("error", "Unknown error")
            print(f"\n{host_name}: FAILED - {error}")
            continue
        
        neighbors = host_result.result.get("data", {})
        if not neighbors:
            print(f"\n{host_name}: No LLDP neighbors discovered")
            continue
        
        print(f"\n{host_name}:")
        for local_int in sorted(neighbors.keys()):
            print(f"  {local_int}:")
            for neighbor in neighbors[local_int]:
                remote_device = neighbor.get("hostname", "Unknown")
                remote_int = neighbor.get("port", "Unknown")
                print(f"    → {remote_device} ({remote_int})")
    
    print("\n" + "=" * 70 + "\n")


def print_json_report(results: Dict) -> None:
    """Print JSON-formatted topology data."""
    output = {}
    for host_name in sorted(results.keys()):
        host_result = results[host_name][0]
        output[host_name] = host_result.result
    print(json.dumps(output, indent=2))


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Discover network topology using LLDP",
        epilog="Example: %(prog)s -i inv -u admin -p secret --format json"
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory",
        help="Nornir inventory location (default: inventory)"
    )
    parser.add_argument(
        "-u", "--username",
        required=True,
        help="Device username for authentication"
    )
    parser.add_argument(
        "-p", "--password",
        required=True,
        help="Device password for authentication"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--filter",
        help="Filter devices using Nornir filter (e.g., site:us)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )
    
    try:
        logger.info("Initializing Nornir inventory")
        nr = InitNornir(config_file="config.yaml")
        
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password
        
        if args.filter:
            logger.info(f"Applying filter: {args.filter}")
            nr = nr.filter(F(args.filter))
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter criteria")
            return 1
        
        logger.info(f"Starting LLDP discovery on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=collect_lldp_neighbors)
        
        if args.format == "json":
            print_json_report(results)
        else:
            print_text_report(results)
        
        logger.info("LLDP discovery completed successfully")
        return 0
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```