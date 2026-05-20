```python
"""
vlan_report_v3.py - Cross-Device VLAN Trunk Consistency Report

Purpose:
    Queries trunk interfaces across multiple switches and identifies VLAN
    allowed-list mismatches. Catches the common operational mistake of adding
    a VLAN on one trunk end without updating the peer, which causes silent
    traffic drops for that VLAN across the network.

Usage:
    python vlan_report_v3.py --hosts-file hosts.yaml --groups-file groups.yaml
    python vlan_report_v3.py --hosts-file hosts.yaml --username admin --password secret
    python vlan_report_v3.py --hosts-file hosts.yaml --vlan 100
    python vlan_report_v3.py --hosts-file hosts.yaml --strict

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Cisco IOS/IOS-XE devices with SSH access.
    hosts.yaml and groups.yaml in Nornir simple inventory format.
"""

import argparse
import logging
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)


def expand_vlan_range(vlan_str: str) -> Set[int]:
    """Expand a Cisco VLAN range string like '1-5,10,20-25' into a set of ints."""
    vlans: Set[int] = set()
    if not vlan_str or vlan_str.strip() in ("none", "ALL"):
        return vlans
    for part in vlan_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                vlans.update(range(int(start), int(end) + 1))
            except ValueError:
                logger.debug("Could not parse VLAN range segment: %s", part)
        else:
            try:
                vlans.add(int(part))
            except ValueError:
                logger.debug("Could not parse VLAN id: %s", part)
    return vlans


def parse_trunk_vlans(output: str) -> Dict[str, Set[int]]:
    """Parse `show interfaces trunk` output into {interface: set_of_allowed_vlans}."""
    port_vlans: Dict[str, Set[int]] = {}
    lines = output.strip().splitlines()
    in_allowed = False
    for line in lines:
        if "VLANs allowed on trunk" in line:
            in_allowed = True
            continue
        if in_allowed:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Port"):
                # Next section — only capture the allowed-on-trunk section
                break
            parts = stripped.split()
            if len(parts) >= 2:
                port_vlans[parts[0]] = expand_vlan_range(parts[1])
    return port_vlans


def collect_trunk_data(task: Task) -> Result:
    r = task.run(
        task=netmiko_send_command,
        command_string="show interfaces trunk",
        name="show interfaces trunk",
    )
    return Result(host=task.host, result=r.result)


def find_mismatches(
    device_data: Dict[str, Dict[str, Set[int]]],
    filter_vlan: Optional[int],
) -> List[Dict]:
    """
    Identify VLANs present on some devices' trunk ports but not others.
    Returns a list of mismatch records sorted by VLAN ID.
    """
    vlan_locations: Dict[int, List[str]] = defaultdict(list)
    for device, trunk_map in device_data.items():
        for interface, vlans in trunk_map.items():
            for vlan in vlans:
                vlan_locations[vlan].append(f"{device}:{interface}")

    all_devices = set(device_data.keys())
    issues = []

    candidates = [filter_vlan] if filter_vlan else sorted(vlan_locations)
    for vlan in candidates:
        locations = vlan_locations.get(vlan, [])
        devices_with = {loc.split(":")[0] for loc in locations}
        missing = sorted(all_devices - devices_with)
        if missing:
            issues.append({
                "vlan": vlan,
                "present_on": sorted(locations),
                "absent_from": missing,
            })

    return issues


def print_report(
    device_data: Dict[str, Dict[str, Set[int]]],
    issues: List[Dict],
    filter_vlan: Optional[int],
) -> None:
    print("\n" + "=" * 68)
    print("VLAN TRUNK CONSISTENCY REPORT")
    print("=" * 68)
    print(f"\nDevices queried: {len(device_data)}")
    for device, trunk_map in sorted(device_data.items()):
        unique_vlans = len({v for vlans in trunk_map.values() for v in vlans})
        print(f"  {device}: {len(trunk_map)} trunk port(s), {unique_vlans} unique VLAN(s)")
    print(f"\nVLAN MISMATCHES")
    print("-" * 68)
    if not issues:
        target = f"VLAN {filter_vlan}" if filter_vlan else "all VLANs"
        print(f"  No consistency issues found for {target}.")
    else:
        for issue in issues:
            print(f"\n  VLAN {issue['vlan']} carried on:")
            for loc in issue["present_on"]:
                print(f"    + {loc}")
            print(f"  Not found on device(s):")
            for dev in issue["absent_from"]:
                print(f"    - {dev}")
    print("\n" + "=" * 68)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-device VLAN trunk consistency report using Nornir"
    )
    parser.add_argument("--hosts-file", default="hosts.yaml")
    parser.add_argument("--groups-file", default="groups.yaml")
    parser.add_argument("--defaults-file", default="defaults.yaml")
    parser.add_argument("--username", help="Override SSH username from inventory")
    parser.add_argument("--password", help="Override SSH password from inventory")
    parser.add_argument("--vlan", type=int, metavar="VLAN_ID",
                        help="Report on a specific VLAN only")
    parser.add_argument("--workers", type=int, default=10,
                        help="Concurrent device connections (default: 10)")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with code 1 if any mismatch is found")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    nr = InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.hosts_file,
                "group_file": args.groups_file,
                "defaults_file": args.defaults_file,
            },
        },
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        logging={"enabled": False},
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    logger.info("Querying %d device(s)", len(nr.inventory.hosts))
    results = nr.run(task=collect_trunk_data, name="Collect trunk VLANs")

    failed = [h for h, r in results.items() if r.failed]
    if failed:
        logger.warning("Failed devices (%d): %s", len(failed), ", ".join(failed))

    device_data: Dict[str, Dict[str, Set[int]]] = {}
    for host, multi in results.items():
        if not multi.failed:
            device_data[host] = parse_trunk_vlans(multi[0].result or "")

    if not device_data:
        print("ERROR: No usable data collected. Check connectivity and credentials.",
              file=sys.stderr)
        sys.exit(2)

    issues = find_mismatches(device_data, filter_vlan=args.vlan)
    print_report(device_data, issues, filter_vlan=args.vlan)

    if args.strict and issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
```