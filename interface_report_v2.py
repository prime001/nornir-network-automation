```python
"""
013_neighbor_discovery.py - Network Device Neighbor Discovery and Topology Report

Purpose:
    Collects LLDP/CDP neighbor information from network devices and generates
    a topology report showing device relationships. Useful for validating
    documented topology, discovering device interconnections, and identifying
    network structure.

Usage:
    python 013_neighbor_discovery.py --devices all --format table
    python 013_neighbor_discovery.py --devices site1 --format json --output neighbors.json
    python 013_neighbor_discovery.py --devices leaf01 --format csv

Prerequisites:
    - Nornir with netmiko transport plugin installed
    - SSH connectivity to all target devices
    - Devices support LLDP (preferred) or CDP
    - Device credentials configured in nornir inventory
    - config.yaml present in working directory

Output Formats:
    table: Human-readable ASCII table (default)
    json: JSON format for programmatic parsing
    csv: CSV format for Excel/spreadsheet import
"""

import argparse
import csv
import json
import logging
import sys
from io import StringIO
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def collect_neighbors(task: Task) -> Result:
    """Retrieve LLDP or CDP neighbor information from device."""
    protocol_used = None
    raw_output = None

    try:
        output = netmiko_send_command(task, command_string="show lldp neighbors detail")
        raw_output = str(output)
        protocol_used = "LLDP"
    except Exception as e:
        logger.debug(f"{task.host.name}: LLDP unavailable, attempting CDP")
        try:
            output = netmiko_send_command(task, command_string="show cdp neighbors detail")
            raw_output = str(output)
            protocol_used = "CDP"
        except Exception as e2:
            logger.warning(f"{task.host.name}: Neither LLDP nor CDP available")
            return Result(host=task.host, result=[], failed=True)

    neighbors = _parse_neighbor_data(raw_output, task.host.name) if raw_output else []

    return Result(
        host=task.host,
        result={"device": task.host.name, "neighbors": neighbors, "protocol": protocol_used}
    )


def _parse_neighbor_data(output: str, local_device: str) -> List[Dict]:
    """Parse LLDP/CDP output to extract neighbor relationships."""
    neighbors = []
    current_neighbor = {}

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            if "neighbor_device" in current_neighbor:
                neighbors.append(current_neighbor)
            current_neighbor = {}
            continue

        if "Device ID" in line or "System Name" in line:
            value = line.split(":", 1)[-1].strip() if ":" in line else ""
            current_neighbor["neighbor_device"] = value or "unknown"

        elif "Local Intf" in line or ("Interface" in line and "Local" in line):
            value = line.split(":", 1)[-1].strip() if ":" in line else ""
            current_neighbor["local_interface"] = value or "unknown"

        elif "Remote Intf" in line or ("Port" in line and "Remote" in line):
            value = line.split(":", 1)[-1].strip() if ":" in line else ""
            current_neighbor["neighbor_interface"] = value or "unknown"

    if "neighbor_device" in current_neighbor:
        neighbors.append(current_neighbor)

    # Format results consistently
    formatted = []
    for n in neighbors:
        formatted.append({
            "local_device": local_device,
            "local_interface": n.get("local_interface", "unknown"),
            "neighbor_device": n.get("neighbor_device", "unknown"),
            "neighbor_interface": n.get("neighbor_interface", "unknown")
        })

    return formatted


def format_table(topology: List[Dict]) -> str:
    """Format topology as ASCII table."""
    if not topology:
        return "No neighbor relationships discovered"

    header = "Local Device     Local Interface  Neighbor Device  Neighbor Interface"
    sep = "-" * 80
    lines = [sep, header, sep]

    for item in topology:
        lines.append(
            f"{item['local_device']:<16} "
            f"{item['local_interface']:<16} "
            f"{item['neighbor_device']:<16} "
            f"{item['neighbor_interface']:<16}"
        )

    lines.append(sep)
    return "\n".join(lines)


def format_json(topology: List[Dict]) -> str:
    """Format topology as JSON."""
    return json.dumps(topology, indent=2)


def format_csv(topology: List[Dict]) -> str:
    """Format topology as CSV."""
    if not topology:
        return ""

    output = StringIO()
    fieldnames = ["local_device", "local_interface", "neighbor_device", "neighbor_interface"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(topology)

    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description="Discover network device neighbors via LLDP/CDP and generate topology report"
    )
    parser.add_argument(
        "--devices",
        default="all",
        help="Device filter: 'all', group name, or device name (default: all)"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--output",
        help="Write report to file (default: stdout)"
    )
    args = parser.parse_args()

    try:
        nr = InitNornir(config_file="config.yaml")
        logger.info(f"Loaded inventory: {len(nr.inventory.hosts)} devices")
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        sys.exit(1)

    # Apply device filter
    if args.devices != "all":
        filter_expr = F(groups__contains=args.devices) | F(name=args.devices)
        nr = nr.filter(filter_expr)

    if not nr.inventory.hosts:
        logger.error("No devices matched the filter criteria")
        sys.exit(1)

    logger.info(f"Running discovery on {len(nr.inventory.hosts)} devices")
    results = nr.run(task=collect_neighbors, num_workers=5)

    # Aggregate topology data
    topology_data = []
    for host_name, task_result in results.items():
        for result in task_result:
            if not result.failed:
                data = result.result
                topology_data.extend(data["neighbors"])
                neighbor_count = len(data["neighbors"])
                logger.info(f"{host_name}: {neighbor_count} neighbors ({data['protocol']})")
            else:
                logger.warning(f"{host_name}: Discovery failed")

    # Format output
    if args.format == "table":
        output = format_table(topology_data)
    elif args.format == "json":
        output = format_json(topology_data)
    else:
        output = format_csv(topology_data)

    # Write to file or stdout
    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(output)
            logger.info(f"Report saved to {args.output}")
        except IOError as e:
            logger.error(f"Failed to write output file: {e}")
            sys.exit(1)
    else:
        print(output)

    logger.info(f"Discovery complete: {len(topology_data)} relationships found")


if __name__ == "__main__":
    main()
```