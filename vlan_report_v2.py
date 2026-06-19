```python
"""
VLAN Cross-Device Consistency Checker

Purpose:
    Collect VLAN databases and trunk configurations from multiple switches via
    nornir/netmiko, then cross-compare to identify fleet-wide inconsistencies:
    VLANs present on some devices but missing from others, VLAN name mismatches
    for the same ID across switches, and trunk-allowed VLANs that have no
    corresponding entry in the local VLAN database.

    Useful before planned maintenance, after network changes, or as part of
    a periodic layer-2 audit.

Usage:
    python 036_vlan_consistency.py \
        --hosts inventory/hosts.yaml \
        --groups inventory/groups.yaml \
        --defaults inventory/defaults.yaml \
        [--filter-group campus_switches] \
        [--output report.json] \
        [--format table|json] \
        [--workers 10]

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils tabulate
    Inventory files with valid credentials; devices must support
    'show vlan brief' and 'show interfaces trunk' (IOS/IOS-XE/NX-OS).
"""

import argparse
import json
import logging
import re
import sys
from typing import Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Legacy FDDI/TR VLANs that appear in show vlan but are not user-configurable
_SKIP_VLANS = {1002, 1003, 1004, 1005}


def parse_vlan_brief(output: str) -> dict[int, str]:
    vlans: dict[int, str] = {}
    for line in output.splitlines():
        m = re.match(r"^(\d+)\s+(\S+)", line)
        if m:
            vid = int(m.group(1))
            if vid not in _SKIP_VLANS:
                vlans[vid] = m.group(2)
    return vlans


def parse_trunk_allowed_vlans(output: str) -> dict[str, set[int]]:
    """Parse 'show interfaces trunk'; return {iface: {allowed_vlan_ids}}."""
    trunks: dict[str, set[int]] = {}
    current_iface: str | None = None
    capture_next = False

    for line in output.splitlines():
        if re.match(r"^(?:Gi|Fa|Te|Hu|Et|Po)\S+\s+", line) and "vlans" not in line.lower():
            current_iface = line.split()[0]
            trunks.setdefault(current_iface, set())
            capture_next = False
            continue

        if "vlans allowed on trunk" in line.lower():
            capture_next = True
            continue

        if capture_next and current_iface:
            stripped = line.strip()
            if re.match(r"^[\d,\-]+$", stripped):
                capture_next = False
                for part in stripped.split(","):
                    part = part.strip()
                    if "-" in part:
                        lo, hi = part.split("-", 1)
                        trunks[current_iface].update(range(int(lo), int(hi) + 1))
                    elif part.isdigit():
                        trunks[current_iface].add(int(part))
            elif stripped:
                capture_next = False

    return trunks


def collect_vlan_data(task: Task) -> Result:
    vlan_out = task.run(
        task=netmiko_send_command,
        command_string="show vlan brief",
    )
    trunk_out = task.run(
        task=netmiko_send_command,
        command_string="show interfaces trunk",
    )

    vlans = parse_vlan_brief(vlan_out.result)
    trunks = parse_trunk_allowed_vlans(trunk_out.result)

    orphan_trunk_vlans: dict[str, list[int]] = {}
    for iface, allowed in trunks.items():
        orphans = sorted(v for v in allowed if v not in vlans and v != 1)
        if orphans:
            orphan_trunk_vlans[iface] = orphans

    return Result(
        host=task.host,
        result={
            "vlans": vlans,
            "trunk_interfaces": {k: sorted(v) for k, v in trunks.items()},
            "orphan_trunk_vlans": orphan_trunk_vlans,
        },
    )


def analyze_consistency(device_data: dict[str, Any]) -> dict[str, Any]:
    all_vids: set[int] = set()
    for data in device_data.values():
        all_vids.update(data["vlans"].keys())

    missing: dict[int, list[str]] = {}
    name_conflicts: dict[int, dict[str, str]] = {}

    for vid in sorted(all_vids):
        absent = [h for h, d in device_data.items() if vid not in d["vlans"]]
        if absent:
            missing[vid] = absent

        names = {h: d["vlans"][vid] for h, d in device_data.items() if vid in d["vlans"]}
        if len(set(names.values())) > 1:
            name_conflicts[vid] = names

    orphan_summary = {
        host: data["orphan_trunk_vlans"]
        for host, data in device_data.items()
        if data["orphan_trunk_vlans"]
    }

    return {
        "devices": sorted(device_data.keys()),
        "total_unique_vlans": len(all_vids),
        "missing_vlans": {str(k): v for k, v in missing.items()},
        "name_conflicts": {str(k): v for k, v in name_conflicts.items()},
        "orphan_trunk_vlans": orphan_summary,
    }


def print_table_report(report: dict[str, Any]) -> None:
    try:
        from tabulate import tabulate
    except ImportError:
        print(json.dumps(report, indent=2))
        return

    print(f"\nDevices: {', '.join(report['devices'])}")
    print(f"Unique VLANs across fleet: {report['total_unique_vlans']}\n")

    if report["missing_vlans"]:
        print("=== VLANs Missing From Some Devices ===")
        rows = [[vid, ", ".join(hosts)] for vid, hosts in sorted(report["missing_vlans"].items())]
        print(tabulate(rows, headers=["VLAN ID", "Missing From"], tablefmt="github"))
        print()

    if report["name_conflicts"]:
        print("=== VLAN Name Conflicts ===")
        rows = []
        for vid, hosts in sorted(report["name_conflicts"].items()):
            for host, name in hosts.items():
                rows.append([vid, host, name])
        print(tabulate(rows, headers=["VLAN ID", "Device", "Name"], tablefmt="github"))
        print()

    if report["orphan_trunk_vlans"]:
        print("=== Trunk VLANs Not in Local Database ===")
        rows = []
        for host, ifaces in sorted(report["orphan_trunk_vlans"].items()):
            for iface, vlans in ifaces.items():
                display = ", ".join(str(v) for v in vlans[:12])
                if len(vlans) > 12:
                    display += f" … (+{len(vlans) - 12})"
                rows.append([host, iface, display])
        print(tabulate(rows, headers=["Device", "Interface", "Orphan VLANs"], tablefmt="github"))
        print()

    total_issues = (
        len(report["missing_vlans"])
        + len(report["name_conflicts"])
        + sum(len(v) for v in report["orphan_trunk_vlans"].values())
    )
    print(f"Total issue types found: {total_issues}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit VLAN consistency across a nornir-managed switch fleet"
    )
    parser.add_argument("--hosts", default="inventory/hosts.yaml")
    parser.add_argument("--groups", default="inventory/groups.yaml")
    parser.add_argument("--defaults", default="inventory/defaults.yaml")
    parser.add_argument("--filter-group", help="Limit to devices in this nornir group")
    parser.add_argument("--output", help="Write JSON report to this file path")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent SSH threads")
    args = parser.parse_args()

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.hosts,
                "group_file": args.groups,
                "defaults_file": args.defaults,
            },
        },
    )

    if args.filter_group:
        nr = nr.filter(lambda h: args.filter_group in [g.name for g in h.groups.values()])

    if not nr.inventory.hosts:
        logger.error("No hosts matched after filtering.")
        return 1

    logger.info("Running against %d host(s)", len(nr.inventory.hosts))
    results = nr.run(task=collect_vlan_data, name="vlan_consistency_check")

    failed = [h for h, r in results.items() if r.failed]
    if failed:
        logger.warning("Collection failed for: %s", ", ".join(failed))

    device_data = {
        host: r[0].result
        for host, r in results.items()
        if not r.failed and r[0].result
    }

    if not device_data:
        logger.error("No usable data collected from any device.")
        return 1

    report = analyze_consistency(device_data)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"Report written to {args.output}")

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print_table_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```