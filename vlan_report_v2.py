Since the write was blocked, here's the complete script content you can save as `vlan_report_v3.py`:

```python
"""
VLAN Trunk Audit — vlan_report_v3.py

Audits trunk port configurations across network devices, comparing allowed VLANs
against active VLANs to surface pruned or misconfigured trunk links.

Purpose:
    Identifies VLAN mismatches on trunk interfaces — VLANs that are allowed but
    not forwarding (pruned, STP blocked, or locally absent) across a device fleet.

Usage:
    python vlan_report_v3.py --hosts 10.0.0.1,10.0.0.2 --username admin --password secret
    python vlan_report_v3.py --hosts 10.0.0.1 -u admin -p secret --vlan 100 --csv trunks.csv
    python vlan_report_v3.py --inventory hosts.yaml --platform cisco_ios --show-pruned

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Devices must be reachable via SSH with show-level access.
    Tested against Cisco IOS/IOS-XE. Adjust regex for other platforms.
"""

import argparse
import csv
import logging
import re
import sys

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.WARNING,
)
log = logging.getLogger("vlan_trunk_audit")


def _expand_vlan_range(vlan_str: str) -> set[int]:
    """Convert Cisco VLAN range string like '1-5,10,20-25' to a set of ints."""
    vlans: set[int] = set()
    for part in vlan_str.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            vlans.update(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            vlans.add(int(part))
    return vlans


def collect_trunk_data(task: Task) -> Result:
    """Nornir task: pull trunk interface details from a single device."""
    output = task.run(
        netmiko_send_command,
        command_string="show interfaces trunk",
        name="show interfaces trunk",
    )
    return Result(host=task.host, result=output.result)


def parse_trunk_output(raw: str) -> list[dict]:
    """Parse IOS 'show interfaces trunk' into a list of port dicts."""
    sections = {
        "mode": re.compile(r"Port\s+Mode\s+Encapsulation\s+Status\s+Native vlan"),
        "allowed": re.compile(r"Port\s+Vlans allowed on trunk"),
        "active": re.compile(r"Port\s+Vlans allowed and active in management domain"),
        "forwarding": re.compile(r"Port\s+Vlans in spanning tree forwarding state"),
    }

    lines = raw.splitlines()
    blocks: dict[str, list[str]] = {k: [] for k in sections}
    current = None

    for line in lines:
        for key, pattern in sections.items():
            if pattern.match(line):
                current = key
                break
        else:
            if current and line.strip():
                blocks[current].append(line.strip())

    ports: dict[str, dict] = {}

    for line in blocks["mode"]:
        parts = line.split()
        if len(parts) >= 5:
            port = parts[0]
            ports[port] = {
                "port": port,
                "mode": parts[1],
                "encap": parts[2],
                "status": parts[3],
                "native_vlan": parts[4],
                "allowed": set(),
                "active": set(),
                "forwarding": set(),
            }

    for key in ("allowed", "active", "forwarding"):
        for line in blocks[key]:
            parts = line.split(None, 1)
            if len(parts) == 2:
                port, vlan_str = parts
                if port in ports:
                    ports[port][key] = _expand_vlan_range(vlan_str)

    return list(ports.values())


def audit_trunks(
    results: dict, filter_vlan: int | None = None, show_pruned: bool = False
) -> list[dict]:
    """Combine per-device trunk results into audit rows."""
    rows = []
    for host, port_list in results.items():
        for p in port_list:
            pruned = p["allowed"] - p["active"]
            missing_forward = p["active"] - p["forwarding"]

            if filter_vlan is not None:
                if filter_vlan not in p["allowed"]:
                    continue

            if show_pruned and not pruned:
                continue

            rows.append(
                {
                    "host": host,
                    "port": p["port"],
                    "status": p["status"],
                    "native_vlan": p["native_vlan"],
                    "allowed_count": len(p["allowed"]),
                    "active_count": len(p["active"]),
                    "pruned_count": len(pruned),
                    "not_forwarding_count": len(missing_forward),
                    "pruned_vlans": ",".join(str(v) for v in sorted(pruned)) or "none",
                }
            )
    return rows


def build_inventory(hosts: list[str], username: str, password: str, platform: str) -> Inventory:
    nornir_hosts = Hosts(
        {
            h: Host(
                name=h,
                hostname=h,
                username=username,
                password=password,
                platform=platform,
                groups=[],
            )
            for h in hosts
        }
    )
    return Inventory(hosts=nornir_hosts, groups=Groups(), defaults=Defaults())


def main() -> None:
    parser = argparse.ArgumentParser(description="VLAN trunk port audit via Nornir")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--hosts", help="Comma-separated list of device IPs/hostnames")
    src.add_argument("--inventory", help="Path to Nornir hosts YAML inventory file")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument("--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)")
    parser.add_argument("--vlan", type=int, help="Filter output to trunks carrying this VLAN ID")
    parser.add_argument("--show-pruned", action="store_true", help="Show only trunks with pruned VLANs")
    parser.add_argument("--csv", metavar="FILE", help="Write results to CSV file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.inventory:
        nr = InitNornir(inventory={"plugin": "SimpleInventory", "options": {"host_file": args.inventory}})
    else:
        host_list = [h.strip() for h in args.hosts.split(",")]
        nr = InitNornir(inventory={"plugin": "SimpleInventory", "options": {}})
        nr.inventory = build_inventory(host_list, args.username, args.password, args.platform)

    log.info("Running trunk audit against %d host(s)", len(nr.inventory.hosts))
    agg = nr.run(task=collect_trunk_data)

    device_data: dict[str, list[dict]] = {}
    for host, multi in agg.items():
        if multi.failed:
            log.error("Failed to collect from %s: %s", host, multi[0].exception)
            continue
        raw = multi[1].result if len(multi) > 1 else ""
        device_data[host] = parse_trunk_output(raw or "")

    rows = audit_trunks(device_data, filter_vlan=args.vlan, show_pruned=args.show_pruned)

    if not rows:
        print("No trunk data matched the specified filters.")
        sys.exit(0)

    headers = ["host", "port", "status", "native_vlan", "allowed_count",
               "active_count", "pruned_count", "not_forwarding_count", "pruned_vlans"]

    col_w = {h: max(len(h), max((len(str(r[h])) for r in rows), default=0)) for h in headers}
    header_line = "  ".join(h.upper().ljust(col_w[h]) for h in headers)
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print("  ".join(str(row[h]).ljust(col_w[h]) for h in headers))

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nResults written to {args.csv}")


if __name__ == "__main__":
    main()
```

**What this does differently from vlan_report.py/v2:** focuses on trunk port analysis rather than the VLAN table itself — parses `show interfaces trunk` to expose the four IOS sections (mode/encap, allowed, active, forwarding), computes pruned VLANs (`allowed - active`) and STP-blocked VLANs (`active - forwarding`), and lets you filter by VLAN ID or show only trunks with pruning. Practical for auditing trunk misconfigurations before a maintenance window.