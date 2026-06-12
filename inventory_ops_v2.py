```python
"""
Device Neighbor Discovery and Link Validation Script.

Discovers neighbors using LLDP/CDP via NAPALM, validates bidirectional
relationships, and reports potential link issues or topology problems.

Usage:
    python neighbor_validator.py --inventory inventory.yaml --device switch01
    python neighbor_validator.py --inventory inventory.yaml --username admin --password pass

Prerequisites:
    - nornir with NAPALM plugin
    - LLDP/CDP enabled on all network devices
    - Read-only access to device management interfaces
"""

import argparse
import logging
from collections import defaultdict
from typing import Dict, List

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def get_neighbors(task: Task) -> Result:
    """Retrieve LLDP/CDP neighbors using NAPALM."""
    result = task.run(napalm_get, getters=["lldp_neighbors"])
    return result


def validate_neighbors(inventory_file: str, device: str = None,
                      username: str = None, password: str = None) -> None:
    """
    Discover and validate device neighbors.

    Args:
        inventory_file: Path to nornir inventory file
        device: Specific device to check (optional)
        username: Override inventory username
        password: Override inventory password
    """
    nr = InitNornir(config_file=inventory_file)

    if device:
        nr = nr.filter(name=device)

    if username:
        nr.inventory.defaults.username = username
    if password:
        nr.inventory.defaults.password = password

    logger.info(f"Gathering neighbors from {len(nr.inventory.hosts)} devices")

    results = nr.run(task=get_neighbors)

    neighbors_map: Dict[str, List[Dict]] = defaultdict(list)
    device_neighbors: Dict[str, Dict] = {}

    for host_name, task_result in results.items():
        if not task_result.failed:
            try:
                napalm_result = task_result[0].result
                neighbors = napalm_result.get("lldp_neighbors", {})
                device_neighbors[host_name] = neighbors

                for local_iface, remote_list in neighbors.items():
                    for remote in remote_list:
                        neighbors_map[host_name].append({
                            "local_interface": local_iface,
                            "remote_device": remote["hostname"],
                            "remote_interface": remote["port"],
                        })
                logger.debug(f"{host_name}: Found {len(neighbors_map[host_name])} "
                           f"neighbors")
            except Exception as e:
                logger.error(f"{host_name}: Failed to parse neighbors - {e}")
        else:
            logger.error(f"{host_name}: Task failed - "
                        f"{task_result[0].exception}")

    print("\n" + "="*80)
    print("DEVICE NEIGHBOR DISCOVERY REPORT")
    print("="*80)

    unidirectional = []

    for device_name in sorted(neighbors_map.keys()):
        print(f"\n{device_name}:")
        for neighbor in neighbors_map[device_name]:
            remote = neighbor["remote_device"]
            local_if = neighbor["local_interface"]
            remote_if = neighbor["remote_interface"]

            is_bidirectional = False
            for remote_neighbor in neighbors_map.get(remote, []):
                if (remote_neighbor["remote_device"] == device_name and
                    remote_neighbor["remote_interface"] == local_if):
                    is_bidirectional = True
                    break

            status = "✓ Bidirectional" if is_bidirectional else "✗ Unidirectional"
            print(f"  {local_if} -> {remote} {remote_if}  [{status}]")

            if not is_bidirectional:
                unidirectional.append(
                    f"{device_name}:{local_if} -> {remote}:{remote_if}"
                )

    print("\n" + "="*80)
    print(f"Total devices discovered: {len(neighbors_map)}")
    total_links = sum(len(n) for n in neighbors_map.values())
    print(f"Total neighbor relationships: {total_links}")

    if unidirectional:
        print(f"\nWARNING: {len(unidirectional)} unidirectional link(s) found:")
        for link in unidirectional:
            print(f"  - {link}")
    else:
        print("\n✓ All discovered links are bidirectional")

    print("="*80)


def main() -> None:
    """Parse arguments and execute validation."""
    parser = argparse.ArgumentParser(
        description="Discover and validate device neighbor relationships"
    )
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to nornir inventory file"
    )
    parser.add_argument(
        "--device",
        help="Specific device to validate (optional)"
    )
    parser.add_argument(
        "--username",
        help="Override inventory username"
    )
    parser.add_argument(
        "--password",
        help="Override inventory password"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        validate_neighbors(
            args.inventory,
            device=args.device,
            username=args.username,
            password=args.password
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```