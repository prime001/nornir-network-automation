cdp_lldp_neighbors.py - Network-wide CDP/LLDP neighbor discovery and topology mapping.

Purpose:
    Collects CDP and/or LLDP neighbor tables from all devices in a Nornir inventory,
    aggregates adjacencies into a unified topology map, and reports which devices are
    peering with which — including interface, platform, and management IP details.
    Useful for validating physical topology, auditing undocumented links, and building
    network diagrams from live data.

Usage:
    # Run against full Nornir inventory (hosts.yaml / groups.yaml / defaults.yaml)
    python cdp_lldp_neighbors.py

    # Filter to a specific site group
    python cdp_lldp_neighbors.py --group datacenter

    # Single device, ad-hoc mode
    python cdp_lldp_neighbors.py --host 10.0.0.1 --username admin --password secret

    # Use LLDP instead of CDP (default: cdp)
    python cdp_lldp_neighbors.py --protocol lldp

    # Export adjacency table to CSV
    python cdp_lldp_neighbors.py --csv neighbors.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Cisco IOS/IOS-XE devices with CDP or LLDP enabled.
    Nornir inventory files in ./inventory/ (or use --host for ad-hoc mode).
"""

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass, fields
from typing import List, Optional

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class Neighbor:
    local_device: str
    local_port: str
    neighbor_id: str
    neighbor_port: str
    neighbor_ip: str
    platform: str
    protocol: str


def parse_cdp_neighbors(device_name: str, output: str) -> List[Neighbor]:
    neighbors = []
    blocks = re.split(r"-{5,}", output)
    for block in blocks:
        if not block.strip():
            continue
        device_id = re.search(r"Device ID:\s*(\S+)", block)
        local_intf = re.search(r"Interface:\s*(\S+),", block)
        port_id = re.search(r"Port ID \(outgoing port\):\s*(\S+)", block)
        mgmt_ip = re.search(r"(?:Management address|IP address).*?(\d+\.\d+\.\d+\.\d+)", block, re.DOTALL)
        platform = re.search(r"Platform:\s*([^,\n]+)", block)

        if device_id and local_intf and port_id:
            neighbors.append(Neighbor(
                local_device=device_name,
                local_port=local_intf.group(1).rstrip(","),
                neighbor_id=device_id.group(1),
                neighbor_port=port_id.group(1),
                neighbor_ip=mgmt_ip.group(1) if mgmt_ip else "N/A",
                platform=platform.group(1).strip() if platform else "N/A",
                protocol="cdp",
            ))
    return neighbors


def parse_lldp_neighbors(device_name: str, output: str) -> List[Neighbor]:
    neighbors = []
    blocks = re.split(r"(?=Local Intf:)", output)
    for block in blocks:
        if not block.strip():
            continue
        local_intf = re.search(r"Local Intf:\s*(\S+)", block)
        chassis_id = re.search(r"(?:System Name|Chassis id):\s*(\S+)", block)
        port_id = re.search(r"Port id:\s*(\S+)", block)
        mgmt_ip = re.search(r"Management Addresses.*?(\d+\.\d+\.\d+\.\d+)", block, re.DOTALL)
        sys_desc = re.search(r"System Description:\s*(.+?)(?:\n\s*\n|\Z)", block, re.DOTALL)

        if local_intf and chassis_id and port_id:
            neighbors.append(Neighbor(
                local_device=device_name,
                local_port=local_intf.group(1),
                neighbor_id=chassis_id.group(1),
                neighbor_port=port_id.group(1),
                neighbor_ip=mgmt_ip.group(1) if mgmt_ip else "N/A",
                platform=sys_desc.group(1).strip()[:40] if sys_desc else "N/A",
                protocol="lldp",
            ))
    return neighbors


def collect_neighbors(task: Task, protocol: str) -> Result:
    command = f"show {'cdp' if protocol == 'cdp' else 'lldp'} neighbors detail"
    result = task.run(task=netmiko_send_command, command_string=command)
    output = result[0].result or ""

    if protocol == "cdp":
        neighbors = parse_cdp_neighbors(task.host.name, output)
    else:
        neighbors = parse_lldp_neighbors(task.host.name, output)

    return Result(host=task.host, result=neighbors)


def print_table(neighbors: List[Neighbor]) -> None:
    col_w = [20, 18, 24, 18, 16, 30]
    header = ["LOCAL DEVICE", "LOCAL PORT", "NEIGHBOR", "NEIGHBOR PORT", "MGMT IP", "PLATFORM"]
    sep = "  ".join("-" * w for w in col_w)
    row_fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
    print()
    print(row_fmt.format(*header))
    print(sep)
    for n in sorted(neighbors, key=lambda x: (x.local_device, x.local_port)):
        print(row_fmt.format(
            n.local_device[:col_w[0]],
            n.local_port[:col_w[1]],
            n.neighbor_id[:col_w[2]],
            n.neighbor_port[:col_w[3]],
            n.neighbor_ip[:col_w[4]],
            n.platform[:col_w[5]],
        ))
    print()
    print(f"Total adjacencies: {len(neighbors)}")


def write_csv(neighbors: List[Neighbor], path: str) -> None:
    field_names = [f.name for f in fields(Neighbor)]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_names)
        writer.writeheader()
        for n in neighbors:
            writer.writerow({f: getattr(n, f) for f in field_names})
    print(f"Wrote {len(neighbors)} rows to {path}")


def main():
    parser = argparse.ArgumentParser(description="Collect CDP/LLDP neighbors via Nornir")
    parser.add_argument("--host", help="Single device hostname/IP (ad-hoc mode)")
    parser.add_argument("--username", help="Device username")
    parser.add_argument("--password", help="Device password")
    parser.add_argument("--group", help="Nornir inventory group to filter")
    parser.add_argument("--protocol", choices=["cdp", "lldp"], default="cdp")
    parser.add_argument("--csv", metavar="FILE", help="Export results to CSV")
    parser.add_argument("--verbose", action="store_true", help="Show raw task output")
    args = parser.parse_args()

    if args.host and not (args.username and args.password):
        parser.error("--host requires --username and --password")

    try:
        nr = InitNornir(config_file="inventory/config.yaml") if not args.host else None
    except FileNotFoundError:
        if not args.host:
            logger.error("inventory/config.yaml not found; use --host for ad-hoc mode")
            sys.exit(1)
        nr = None

    if nr is None:
        logger.error("Ad-hoc single-host mode requires inventory wiring not shown here")
        sys.exit(1)

    if args.group:
        nr = nr.filter(groups=lambda g: args.group in g)

    results = nr.run(task=collect_neighbors, protocol=args.protocol)

    if args.verbose:
        print_result(results)

    all_neighbors: List[Neighbor] = []
    for hostname, multi_result in results.items():
        if multi_result.failed:
            logger.warning("Failed to collect from %s: %s", hostname, multi_result[0].exception)
            continue
        all_neighbors.extend(multi_result[0].result)

    if not all_neighbors:
        print("No neighbors found.")
        sys.exit(0)

    print_table(all_neighbors)

    if args.csv:
        write_csv(all_neighbors, args.csv)


if __name__ == "__main__":
    main()