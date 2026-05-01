Good — I have enough context on the style. Writing the script now.

```python
"""
008_ospf_neighbors.py — OSPF Neighbor State Monitor

Purpose:
    Collect OSPF neighbor adjacency states from every device in the Nornir
    inventory in parallel and flag sessions that are not in FULL state.
    Useful as a pre/post-change health check or recurring underlay pulse.

Usage:
    python 008_ospf_neighbors.py
    python 008_ospf_neighbors.py --group core
    python 008_ospf_neighbors.py --group core --non-full-only
    python 008_ospf_neighbors.py --csv ospf_report.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory: inventory/hosts.yaml, inventory/groups.yaml, inventory/defaults.yaml
    Devices must support "show ip ospf neighbor" (Cisco IOS / IOS-XE / NX-OS).
"""

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

NEIGHBOR_RE = re.compile(
    r"(?P<neighbor_id>\d+\.\d+\.\d+\.\d+)\s+"
    r"(?P<priority>\d+)\s+"
    r"(?P<state>\S+)\s+"
    r"(?P<dead_time>\S+)\s+"
    r"(?P<address>\d+\.\d+\.\d+\.\d+)\s+"
    r"(?P<interface>\S+)"
)


@dataclass
class OspfNeighbor:
    device: str
    neighbor_id: str
    state: str
    address: str
    interface: str
    dead_time: str
    is_full: bool = field(init=False)

    def __post_init__(self) -> None:
        self.is_full = self.state.upper().startswith("FULL")


def collect_ospf_neighbors(task: Task) -> Result:
    output = task.run(
        task=netmiko_send_command,
        command_string="show ip ospf neighbor",
        name="show ip ospf neighbor",
    )
    neighbors: List[OspfNeighbor] = []
    for match in NEIGHBOR_RE.finditer(output.result):
        neighbors.append(
            OspfNeighbor(
                device=task.host.name,
                neighbor_id=match.group("neighbor_id"),
                state=match.group("state"),
                address=match.group("address"),
                interface=match.group("interface"),
                dead_time=match.group("dead_time"),
            )
        )
    return Result(host=task.host, result=neighbors)


def write_csv(neighbors: List[OspfNeighbor], path: str) -> None:
    fields = ["device", "neighbor_id", "state", "address", "interface", "dead_time", "is_full"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for n in neighbors:
            writer.writerow(
                {
                    "device": n.device,
                    "neighbor_id": n.neighbor_id,
                    "state": n.state,
                    "address": n.address,
                    "interface": n.interface,
                    "dead_time": n.dead_time,
                    "is_full": n.is_full,
                }
            )
    log.info("CSV written to %s (%d rows)", path, len(neighbors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect OSPF neighbor states from all inventory devices."
    )
    parser.add_argument(
        "--config", default="nornir.yaml", metavar="FILE",
        help="Nornir config file (default: nornir.yaml)",
    )
    parser.add_argument(
        "--group", metavar="GROUP",
        help="Limit to a specific Nornir inventory group",
    )
    parser.add_argument(
        "--non-full-only", action="store_true",
        help="Print only adjacencies that are NOT in FULL state",
    )
    parser.add_argument(
        "--csv", metavar="PATH",
        help="Write full results to a CSV file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    nr = InitNornir(config_file=args.config)
    if args.group:
        nr = nr.filter(group=args.group)

    if not nr.inventory.hosts:
        log.error("No hosts matched (group=%s). Check your inventory.", args.group)
        return 1

    log.info("Running against %d host(s) with %d worker(s)",
             len(nr.inventory.hosts), nr.config.runner.options.get("num_workers", 1))

    results = nr.run(task=collect_ospf_neighbors, name="OSPF neighbor collection")

    all_neighbors: List[OspfNeighbor] = []
    failed_hosts: List[str] = []

    for host, multi_result in results.items():
        if multi_result.failed:
            log.warning("Failed to collect from %s: %s", host, multi_result[0].exception)
            failed_hosts.append(host)
            continue
        neighbors: List[OspfNeighbor] = multi_result[1].result
        all_neighbors.extend(neighbors)
        if not neighbors:
            log.info("%s: no OSPF neighbors found", host)

    display = [n for n in all_neighbors if not n.is_full] if args.non_full_only else all_neighbors

    if display:
        header = f"{'Device':<20} {'Neighbor ID':<16} {'State':<14} {'Address':<16} {'Interface':<18} Dead Timer"
        print("\n" + header)
        print("-" * len(header))
        for n in display:
            flag = "  <-- NOT FULL" if not n.is_full else ""
            print(f"{n.device:<20} {n.neighbor_id:<16} {n.state:<14} {n.address:<16} {n.interface:<18} {n.dead_time}{flag}")
    else:
        state_label = "non-FULL " if args.non_full_only else ""
        print(f"\nNo {state_label}OSPF neighbors to display.")

    non_full = [n for n in all_neighbors if not n.is_full]
    print(f"\nSummary: {len(all_neighbors)} total adjacencies, "
          f"{len(non_full)} not FULL, {len(failed_hosts)} device(s) unreachable.")

    if args.csv:
        write_csv(all_neighbors, args.csv)

    return 1 if non_full or failed_hosts else 0


if __name__ == "__main__":
    sys.exit(main())
```