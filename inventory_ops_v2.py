Now I have enough context. The existing scripts cover: BGP summary, interface reports, config backup, result filtering, compliance/health audit, VLAN reporting, threaded execution, inventory ops, and custom plugins. I'll write a CDP/LLDP neighbor discovery script — practical, portfolio-worthy, and not covered.

```python
"""
CDP/LLDP Neighbor Discovery Script

Purpose:
    Discovers and maps network neighbors across the inventory using
    CDP (Cisco Discovery Protocol) or LLDP (Link Layer Discovery Protocol).
    Builds a topology table showing device adjacencies, interface connections,
    remote device capabilities, and management addresses — useful for
    verifying cabling, auditing topology changes, and bootstrapping CMDB data.

Usage:
    python neighbor_discovery.py
    python neighbor_discovery.py --protocol lldp --group access-switches
    python neighbor_discovery.py --device core-sw1 --output neighbors.json
    python neighbor_discovery.py --protocol cdp --config /etc/nornir/config.yaml

Prerequisites:
    - pip install nornir nornir-netmiko
    - Inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
    - CDP or LLDP enabled on target devices
    - Devices reachable via SSH
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from typing import Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CDP_COMMAND = "show cdp neighbors detail"
LLDP_COMMAND = "show lldp neighbors detail"


def parse_cdp_neighbors(output: str) -> list[dict]:
    """Parse 'show cdp neighbors detail' into structured records."""
    neighbors = []
    for block in re.split(r"-{10,}", output):
        block = block.strip()
        if not block:
            continue

        entry: dict[str, Any] = {}

        m = re.search(r"Device ID:\s*(.+)", block)
        if m:
            entry["remote_device"] = m.group(1).strip()

        m = re.search(r"Interface:\s*(\S+),\s*Port ID.*?:\s*(\S+)", block)
        if m:
            entry["local_intf"] = m.group(1)
            entry["remote_intf"] = m.group(2)

        m = re.search(r"IP address:\s*(\S+)", block, re.IGNORECASE)
        if m:
            entry["mgmt_address"] = m.group(1)

        m = re.search(r"Platform:\s*([^,]+)", block)
        if m:
            entry["platform"] = m.group(1).strip()

        m = re.search(r"Capabilities:\s*(.+)", block)
        if m:
            entry["capabilities"] = m.group(1).strip()

        if entry.get("remote_device"):
            neighbors.append(entry)

    return neighbors


def parse_lldp_neighbors(output: str) -> list[dict]:
    """Parse 'show lldp neighbors detail' into structured records."""
    neighbors = []
    for block in re.split(r"-{10,}|(?=Local Intf:)", output):
        block = block.strip()
        if not block or "Total entries" in block:
            continue

        entry: dict[str, Any] = {}

        m = re.search(r"System Name:\s*(.+)", block)
        if m:
            entry["remote_device"] = m.group(1).strip()

        m = re.search(r"Local Intf:\s*(\S+)", block)
        if m:
            entry["local_intf"] = m.group(1)

        m = re.search(r"Port id:\s*(\S+)", block)
        if m:
            entry["remote_intf"] = m.group(1)

        m = re.search(r"Management Addresses.*?IP:\s*(\S+)", block, re.DOTALL)
        if m:
            entry["mgmt_address"] = m.group(1)

        m = re.search(r"System Capabilities:\s*(.+)", block)
        if m:
            entry["capabilities"] = m.group(1).strip()

        m = re.search(r"System Description.*?:\s*(.+?)(?:\n\s*\n|$)", block, re.DOTALL)
        if m:
            entry["platform"] = m.group(1).strip().replace("\n", " ")[:60]

        if entry.get("remote_device"):
            neighbors.append(entry)

    return neighbors


def collect_neighbors(task: Task, protocol: str) -> Result:
    """Nornir task: gather CDP/LLDP neighbor data from a single device."""
    command = CDP_COMMAND if protocol == "cdp" else LLDP_COMMAND
    result = task.run(
        task=netmiko_send_command,
        command_string=command,
        use_textfsm=False,
    )
    raw = result.result
    neighbors = parse_cdp_neighbors(raw) if protocol == "cdp" else parse_lldp_neighbors(raw)
    return Result(host=task.host, result=neighbors)


def print_table(all_neighbors: dict[str, list[dict]]) -> None:
    """Render neighbor adjacency data as an aligned console table."""
    w = {"host": 18, "local": 18, "remote_dev": 28, "remote_intf": 20, "mgmt": 16, "caps": 22}
    header = (
        f"{'Local Device':<{w['host']}} "
        f"{'Local Intf':<{w['local']}} "
        f"{'Remote Device':<{w['remote_dev']}} "
        f"{'Remote Intf':<{w['remote_intf']}} "
        f"{'Mgmt IP':<{w['mgmt']}} "
        f"{'Capabilities':<{w['caps']}}"
    )
    sep = "-" * len(header)
    print(f"\n{sep}\n{header}\n{sep}")

    total = 0
    for host, neighbors in sorted(all_neighbors.items()):
        if not neighbors:
            print(f"{host:<{w['host']}} (no neighbors found)")
            continue
        for n in neighbors:
            print(
                f"{host:<{w['host']}} "
                f"{n.get('local_intf', 'N/A'):<{w['local']}} "
                f"{n.get('remote_device', 'N/A'):<{w['remote_dev']}} "
                f"{n.get('remote_intf', 'N/A'):<{w['remote_intf']}} "
                f"{n.get('mgmt_address', 'N/A'):<{w['mgmt']}} "
                f"{n.get('capabilities', 'N/A'):<{w['caps']}}"
            )
            total += 1

    print(f"{sep}\nTotal adjacencies: {total}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover CDP/LLDP neighbors across a Nornir inventory"
    )
    parser.add_argument(
        "--protocol",
        choices=["cdp", "lldp"],
        default="cdp",
        help="Discovery protocol (default: cdp)",
    )
    parser.add_argument("--group", help="Filter inventory to a specific group")
    parser.add_argument("--device", help="Target a single device by hostname")
    parser.add_argument("--output", metavar="FILE", help="Write results to a JSON file")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to Nornir config file (default: config.yaml)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config)
    except FileNotFoundError:
        logger.error("Nornir config file '%s' not found", args.config)
        sys.exit(1)

    if args.group:
        nr = nr.filter(F(groups__contains=args.group))
    if args.device:
        nr = nr.filter(F(name=args.device))

    if not nr.inventory.hosts:
        logger.error("No devices matched the specified filters")
        sys.exit(1)

    logger.info(
        "Running %s neighbor discovery on %d device(s)",
        args.protocol.upper(),
        len(nr.inventory.hosts),
    )

    results = nr.run(task=collect_neighbors, protocol=args.protocol)

    all_neighbors: dict[str, list[dict]] = {}
    failed: list[str] = []

    for host_name, result in results.items():
        if result.failed:
            logger.error("%s: task failed — %s", host_name, result.exception)
            failed.append(host_name)
            all_neighbors[host_name] = []
        else:
            # index 1 is the inner task result when using aggregate tasks
            inner = result[1] if len(result) > 1 else result[0]
            all_neighbors[host_name] = inner.result if isinstance(inner.result, list) else []

    print_table(all_neighbors)

    if failed:
        logger.warning("Failed on %d device(s): %s", len(failed), ", ".join(failed))

    if args.output:
        payload = {
            "generated": datetime.now().isoformat(),
            "protocol": args.protocol,
            "neighbors": all_neighbors,
        }
        with open(args.output, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.info("Results written to %s", args.output)


if __name__ == "__main__":
    main()
```