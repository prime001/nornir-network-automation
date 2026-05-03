```python
"""BGP Neighbor Summary Script

Connects to network devices via Nornir and generates a comprehensive BGP neighbor
summary report. Supports Cisco IOS/XE, Juniper, and other platforms via NAPALM.

Usage:
    python bgp_summary.py --group production --format table
    python bgp_summary.py --devices router1 router2 --format json

Prerequisites:
    - Nornir inventory configured at ./nornir_config.yaml or NORNIR_CONFIG_FILE env var
    - Device credentials configured in inventory
    - NAPALM driver installed for target device types
    - Python packages: nornir, napalm, pyyaml

Output:
    - Displays BGP neighbor status, uptime, and route counts
    - Supports table (human-readable) and JSON formats
    - Returns exit code 0 on success, 1 on failure
"""

import json
import logging
import argparse
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_bgp_neighbors(task) -> Dict[str, Any]:
    """Retrieve BGP neighbors using NAPALM getter."""
    try:
        result = task.run(napalm_get, getters=["bgp_neighbors"])
        return result.result.get("bgp_neighbors", {})
    except Exception as e:
        logger.error(f"Failed to get BGP neighbors: {e}")
        return {}


def print_table_format(device: str, bgp_data: Dict[str, Any]) -> None:
    """Print BGP data in table format."""
    if not bgp_data:
        logger.warning(f"{device}: No BGP data available")
        return

    print(f"\n{'='*110}")
    print(f"BGP Summary: {device}")
    print(f"{'='*110}")
    print(f"{'ASN':<12} {'Neighbor':<18} {'State':<15} {'Uptime':<20} "
          f"{'IPv4 PFX':<12} {'IPv6 PFX':<12}")
    print("-" * 110)

    for asn, neighbors in bgp_data.items():
        for peer_ip, peer_data in neighbors.items():
            state = "UP" if peer_data.get("up", False) else "DOWN"
            uptime = str(peer_data.get("uptime", "N/A"))

            address_families = peer_data.get("address_family", {})
            ipv4_pfx = address_families.get("ipv4", {}).get("sent_prefixes", 0)
            ipv6_pfx = address_families.get("ipv6", {}).get("sent_prefixes", 0)

            print(f"{asn:<12} {peer_ip:<18} {state:<15} {uptime:<20} "
                  f"{ipv4_pfx:<12} {ipv6_pfx:<12}")

    print("=" * 110)


def print_json_format(device: str, bgp_data: Dict[str, Any]) -> None:
    """Print BGP data in JSON format."""
    output = {
        "device": device,
        "bgp_neighbors": bgp_data
    }
    print(json.dumps(output, indent=2, default=str))


def main() -> int:
    """Execute BGP summary retrieval and reporting."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--group",
        type=str,
        help="Filter devices by inventory group"
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        help="Specific device names (space-separated)"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if not args.group and not args.devices:
        logger.error("Specify either --group or --devices")
        parser.print_help()
        return 1

    try:
        nr = InitNornir(config_file="nornir_config.yaml")

        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
        else:
            nr = nr.filter(F(name__in=args.devices))

        if len(nr.inventory.hosts) == 0:
            logger.error("No devices matched selection criteria")
            return 1

        logger.info(f"Processing {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=get_bgp_neighbors)

        failed_count = 0
        success_count = 0

        for device_name in results.keys():
            task_result = results[device_name]

            if task_result.failed:
                logger.error(f"{device_name}: Task failed")
                failed_count += 1
                continue

            bgp_data = task_result[0].result

            if args.format == "table":
                print_table_format(device_name, bgp_data)
            else:
                print_json_format(device_name, bgp_data)

            success_count += 1

        logger.info(f"Completed: {success_count} succeeded, {failed_count} failed")

        return 0 if failed_count == 0 else 1

    except FileNotFoundError:
        logger.error("Nornir config file not found (nornir_config.yaml)")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```