```python
#!/usr/bin/env python3
"""
ARP Table Analyzer - Detect ARP inconsistencies across network devices.

Collects ARP tables from devices and identifies:
- Duplicate IP addresses (same IP, different MACs)
- Broadcast/invalid MAC addresses
- Device-wide ARP statistics

Usage:
    python arp_analyzer.py -i inventory.yaml -u admin -p password
    python arp_analyzer.py -i inventory.yaml -u admin -p password --filter site1
    python arp_analyzer.py -i inventory.yaml -u admin -p password --output json

Prerequisites:
    - nornir and napalm installed
    - Network devices accessible via SSH
    - NAPALM-compatible OS (Cisco IOS, Arista EOS, etc.)
"""

import argparse
import json
import logging
from collections import defaultdict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm_plugins import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_arp_tables(nr, device_filter=None):
    """Retrieve ARP tables from devices using NAPALM."""
    if device_filter:
        nr = nr.filter(F(groups__contains=device_filter))

    logger.info(f"Collecting ARP from {len(nr.inventory.hosts)} devices")
    results = nr.run(task=napalm_get, getters=["arp_table"])
    arp_tables = {}

    for device_name, task_result in results.items():
        if not task_result[0].failed:
            arp_tables[device_name] = task_result[0].result.get("arp_table", {})
        else:
            logger.warning(f"Failed to get ARP from {device_name}")

    logger.info(f"Successfully collected ARP from {len(arp_tables)} devices")
    return arp_tables


def analyze_arp_tables(arp_tables):
    """Analyze ARP tables for inconsistencies and anomalies."""
    duplicates = defaultdict(list)
    broadcast_macs = []
    summary = {}
    ip_map = defaultdict(list)

    for device_name, arp_data in arp_tables.items():
        entry_count = 0

        for vlan_name, arp_entries in arp_data.items():
            for entry in arp_entries:
                ip = entry.get("ip")
                mac = entry.get("mac")

                if not ip or not mac:
                    continue

                entry_count += 1
                ip_map[ip].append({
                    "device": device_name,
                    "mac": mac,
                    "vlan": vlan_name,
                })

                if mac.upper() in ["FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00"]:
                    broadcast_macs.append({
                        "device": device_name,
                        "ip": ip,
                        "mac": mac,
                    })

        summary[device_name] = entry_count

    for ip, entries in ip_map.items():
        unique_macs = set(e["mac"] for e in entries)
        if len(unique_macs) > 1:
            duplicates[ip] = entries

    logger.info(f"Found {len(duplicates)} IPs with multiple MACs")
    return {
        "duplicates": dict(duplicates),
        "broadcast_macs": broadcast_macs,
        "summary": summary,
    }


def format_output(analysis, output_format="text"):
    """Format analysis results for display."""
    if output_format == "json":
        return json.dumps(analysis, indent=2, default=str)

    lines = ["ARP Table Analysis Report", "=" * 50, ""]

    if analysis["duplicates"]:
        lines.append("[!] Duplicate IPs (Multiple MACs):")
        for ip, entries in sorted(analysis["duplicates"].items()):
            lines.append(f"  {ip}:")
            for entry in entries:
                lines.append(
                    f"    {entry['device']} - {entry['mac']} "
                    f"(VLAN {entry['vlan']})"
                )
    else:
        lines.append("[+] No duplicate IPs detected")

    if analysis["broadcast_macs"]:
        lines.append("\n[!] Broadcast/Invalid MAC Addresses:")
        for entry in analysis["broadcast_macs"]:
            lines.append(
                f"  {entry['device']}: {entry['ip']} -> {entry['mac']}"
            )
    else:
        lines.append("\n[+] No broadcast MACs detected")

    lines.append("\nARP Summary (entries per device):")
    for device, count in sorted(analysis["summary"].items()):
        lines.append(f"  {device}: {count} ARP entries")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i", "--inventory", required=True, help="Path to inventory file"
    )
    parser.add_argument(
        "-u", "--username", required=True, help="Device username"
    )
    parser.add_argument(
        "-p", "--password", required=True, help="Device password"
    )
    parser.add_argument(
        "-f", "--filter", help="Filter devices by group name"
    )
    parser.add_argument(
        "-o", "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password

        arp_tables = get_arp_tables(nr, args.filter)
        if not arp_tables:
            logger.error("No ARP tables collected from any device")
            return 1

        analysis = analyze_arp_tables(arp_tables)
        output = format_output(analysis, args.output)
        print(output)

        return 0

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logger.error(f"Script execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```