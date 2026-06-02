```python
"""
LLDP Neighbor Discovery Script

Discovers and reports LLDP neighbors for network devices, displaying device
interconnections and physical topology relationships.

Usage:
    python discover_neighbors.py --device router1 router2 --user admin --password pass
    python discover_neighbors.py --host-file devices.txt --user admin --password pass

Prerequisites:
    - nornir with napalm plugin installed
    - Network devices with LLDP enabled
    - SSH/Telnet connectivity to devices
    - Appropriate credentials with read permissions
"""

import argparse
import logging
import sys
from typing import Dict, List

from nornir import InitNornir
from nornir.core.inventory import Host, Inventory, Groups
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_lldp_data(task: Task) -> Result:
    """Retrieve LLDP neighbor information from device."""
    try:
        result = task.run(napalm_get, getters=["lldp_neighbors"])
        return result
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to retrieve LLDP data - {e}")
        return Result(host=task.host, result={}, failed=True)


def discover_neighbors(
    devices: List[str],
    username: str,
    password: str,
    timeout: int = 30
) -> Dict[str, Dict]:
    """
    Discover LLDP neighbors for a list of devices.

    Args:
        devices: List of device hostnames
        username: SSH username
        password: SSH password
        timeout: Connection timeout in seconds

    Returns:
        Dictionary mapping device names to their LLDP neighbor data
    """
    try:
        hosts = {dev: Host(name=dev, hostname=dev) for dev in devices}
        nr = InitNornir(
            inventory={
                "plugin": "SimpleInventory",
                "options": {
                    "host_file": None,
                    "group_file": None,
                    "defaults_file": None
                }
            }
        )
        nr.inventory = Inventory(hosts=hosts, groups=Groups(), defaults={})

        for host in nr.inventory.hosts.values():
            host.username = username
            host.password = password

        logger.info(f"Querying LLDP neighbors on {len(devices)} device(s)...")
        results = nr.run(task=get_lldp_data)

        neighbors = {}
        for device, task_result in results.items():
            if task_result[0].failed:
                logger.warning(f"Skipping {device}: Unable to retrieve LLDP data")
                continue

            lldp_data = task_result[0].result.get("lldp_neighbors", {})
            neighbors[device] = lldp_data

            neighbor_count = sum(len(ifaces) for ifaces in lldp_data.values())
            logger.info(f"{device}: Discovered {neighbor_count} neighbor(s)")

        return neighbors

    except Exception as e:
        logger.error(f"LLDP discovery failed: {e}")
        raise


def display_results(neighbors: Dict[str, Dict]) -> None:
    """Display discovered neighbors in formatted table."""
    if not neighbors or not any(neighbors.values()):
        print("\nNo LLDP neighbors discovered.\n")
        return

    print("\n" + "=" * 105)
    print(
        f"{'Local Device':<20} {'Local Port':<22} "
        f"{'Remote Device':<20} {'Remote Port':<22}"
    )
    print("=" * 105)

    for device in sorted(neighbors.keys()):
        lldp_data = neighbors[device]

        if not lldp_data:
            print(f"{device:<20} {'No neighbors':<22}")
            continue

        for local_port in sorted(lldp_data.keys()):
            for neighbor in lldp_data[local_port]:
                remote_device = neighbor.get("remote_system_name", "Unknown")
                remote_port = neighbor.get("remote_port_description", "Unknown")

                print(
                    f"{device:<20} {local_port:<22} "
                    f"{remote_device:<20} {remote_port:<22}"
                )

    print("=" * 105 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--device",
        action="append",
        dest="devices",
        metavar="HOSTNAME",
        help="Device hostname (can be repeated)"
    )
    parser.add_argument(
        "--host-file", "-f",
        metavar="FILE",
        help="Text file with one hostname per line (comments start with #)"
    )
    parser.add_argument(
        "--user", "-u",
        required=True,
        metavar="USERNAME",
        help="SSH username"
    )
    parser.add_argument(
        "--password", "-p",
        required=True,
        metavar="PASSWORD",
        help="SSH password"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Connection timeout in seconds (default: 30)"
    )

    args = parser.parse_args()

    devices = []

    if args.host_file:
        try:
            with open(args.host_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        devices.append(line)
        except IOError as e:
            logger.error(f"Cannot read host file '{args.host_file}': {e}")
            sys.exit(1)

    if args.devices:
        devices.extend(args.devices)

    if not devices:
        parser.error("No devices specified. Use --device or --host-file")

    devices = list(dict.fromkeys(devices))

    try:
        neighbors = discover_neighbors(
            devices,
            args.user,
            args.password,
            args.timeout
        )
        display_results(neighbors)

        total_neighbors = sum(
            sum(len(ifaces) for ifaces in device_neighbors.values())
            for device_neighbors in neighbors.values()
        )
        logger.info(f"Success: Discovered {total_neighbors} total neighbor relationship(s)")

    except KeyboardInterrupt:
        logger.info("Discovery cancelled by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```