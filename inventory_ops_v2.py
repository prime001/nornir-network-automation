I'll write a CDP/LLDP neighbor discovery script — practical, not covered by existing scripts, and a natural fit for inventory-ops work.

```python
"""neighbor_discovery.py - CDP/LLDP Neighbor Discovery via Nornir

Purpose:
    Queries CDP and/or LLDP neighbor tables across network devices to build
    a point-to-point adjacency map. Useful for validating physical cabling,
    documenting topology, and auditing unexpected device adjacencies.

Usage:
    # Discover CDP neighbors across full inventory:
    python neighbor_discovery.py

    # Use LLDP instead:
    python neighbor_discovery.py --protocol lldp

    # Target a specific Nornir group:
    python neighbor_discovery.py --group core_switches

    # Export to CSV:
    python neighbor_discovery.py --output neighbors.csv

    # Export to JSON:
    python neighbor_discovery.py --output neighbors.json --format json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: hosts.yaml, groups.yaml, defaults.yaml
    Device platforms supported: ios, eos, nxos
"""

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

CDP_CMD = "show cdp neighbors detail"
LLDP_CMD = "show lldp neighbors detail"


def parse_cdp(raw: str, local_host: str) -> list[dict]:
    neighbors = []
    for block in re.split(r"-{5,}", raw):
        device_id = re.search(r"Device ID:\s*(\S+)", block)
        local_intf = re.search(r"Interface:\s*(\S+?),", block)
        remote_intf = re.search(r"Port ID \(outgoing port\):\s*(\S+)", block)
        platform = re.search(r"Platform:\s*([^,\n]+)", block)
        capabilities = re.search(r"Capabilities:\s*([^\n]+)", block)
        mgmt_ip = re.search(
            r"Management address\(es\):\s*\n\s*IP(?:v4)? address:\s*(\S+)", block
        )
        if device_id and local_intf and remote_intf:
            neighbors.append({
                "local_device": local_host,
                "local_interface": local_intf.group(1),
                "neighbor": device_id.group(1),
                "neighbor_interface": remote_intf.group(1),
                "platform": platform.group(1).strip() if platform else "",
                "capabilities": capabilities.group(1).strip() if capabilities else "",
                "mgmt_ip": mgmt_ip.group(1) if mgmt_ip else "",
                "protocol": "CDP",
            })
    return neighbors


def parse_lldp(raw: str, local_host: str) -> list[dict]:
    neighbors = []
    for block in re.split(r"(?=Local Intf:)", raw):
        local_intf = re.search(r"Local Intf:\s*(\S+)", block)
        port_id = re.search(r"Port id:\s*(\S+)", block)
        sys_name = re.search(r"System Name:\s*(\S+)", block)
        sys_desc = re.search(r"System Description:\s*\n?\s*([^\n]+)", block)
        mgmt_ip = re.search(r"Management Addresses:\s*\n\s*IP:\s*(\S+)", block)
        caps = re.search(r"Enabled Capabilities:\s*([^\n]+)", block)
        if local_intf and (sys_name or port_id):
            neighbors.append({
                "local_device": local_host,
                "local_interface": local_intf.group(1),
                "neighbor": sys_name.group(1) if sys_name else "unknown",
                "neighbor_interface": port_id.group(1) if port_id else "",
                "platform": sys_desc.group(1).strip() if sys_desc else "",
                "capabilities": caps.group(1).strip() if caps else "",
                "mgmt_ip": mgmt_ip.group(1) if mgmt_ip else "",
                "protocol": "LLDP",
            })
    return neighbors


def collect_neighbors(task: Task, protocol: str) -> Result:
    cmd = CDP_CMD if protocol == "cdp" else LLDP_CMD
    try:
        r = task.run(task=netmiko_send_command, command_string=cmd, use_textfsm=False)
        raw = r.result
    except Exception as exc:
        log.warning("%s: neighbor query failed: %s", task.host.name, exc)
        return Result(host=task.host, result=[], failed=True)

    if protocol == "cdp":
        neighbors = parse_cdp(raw, task.host.name)
    else:
        neighbors = parse_lldp(raw, task.host.name)

    log.info("%s: found %d neighbor(s)", task.host.name, len(neighbors))
    return Result(host=task.host, result=neighbors)


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No neighbors found.")
        return
    cols = [
        ("local_device", 18),
        ("local_interface", 22),
        ("neighbor", 26),
        ("neighbor_interface", 22),
        ("mgmt_ip", 16),
        ("capabilities", 22),
    ]
    header = "  ".join(label.upper().ljust(w) for label, w in cols)
    sep = "  ".join("-" * w for _, w in cols)
    print(header)
    print(sep)
    for row in sorted(rows, key=lambda x: (x["local_device"], x["local_interface"])):
        print("  ".join(str(row.get(col, "")).ljust(w) for col, w in cols))
    print(f"\nTotal adjacencies: {len(rows)}")


def write_csv(rows: list[dict], path: str) -> None:
    fields = [
        "local_device", "local_interface", "neighbor", "neighbor_interface",
        "mgmt_ip", "platform", "capabilities", "protocol",
    ]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} record(s) to {path}")


def write_json(rows: list[dict], path: str) -> None:
    with open(path, "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"Wrote {len(rows)} record(s) to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map CDP/LLDP adjacencies across Nornir inventory devices"
    )
    parser.add_argument(
        "--protocol", choices=["cdp", "lldp"], default="cdp",
        help="Discovery protocol (default: cdp)",
    )
    parser.add_argument("--group", help="Limit to a Nornir inventory group")
    parser.add_argument("--output", help="Write results to file path")
    parser.add_argument(
        "--format", choices=["csv", "json"], default="csv",
        help="Output file format when --output is set (default: csv)",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Nornir config file (default: config.yaml)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config)
    except FileNotFoundError:
        log.error("Nornir config not found: %s", args.config)
        sys.exit(1)

    if args.group:
        nr = nr.filter(F(groups__contains=args.group))
        if not nr.inventory.hosts:
            log.error("No hosts matched group '%s'", args.group)
            sys.exit(1)

    host_count = len(nr.inventory.hosts)
    print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Running {args.protocol.upper()} "
        f"discovery on {host_count} device(s)...\n"
    )

    results = nr.run(task=collect_neighbors, protocol=args.protocol)

    all_neighbors: list[dict] = []
    failed: list[str] = []

    for host, multi in results.items():
        if multi.failed:
            failed.append(host)
            continue
        all_neighbors.extend(multi[0].result or [])

    print_table(all_neighbors)

    if failed:
        print(f"\nFailed ({len(failed)}): {', '.join(failed)}")

    if args.output:
        if args.format == "json":
            write_json(all_neighbors, args.output)
        else:
            write_csv(all_neighbors, args.output)


if __name__ == "__main__":
    main()
```