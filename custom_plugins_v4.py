```python
"""
arp_table_collector.py - Network-wide ARP Table Collection and Analysis

Purpose:
    Collects ARP tables from network devices and produces a unified
    IP-to-MAC-to-device mapping. Detects duplicate IPs (potential conflicts)
    and duplicate MACs (multi-homed or recently moved hosts).

Usage:
    python arp_table_collector.py --config config.yaml [--filter-group routers] \
        [--output arp_report.csv] [--format {table,csv,json}] [--detect-conflicts]

Prerequisites:
    - nornir>=3.0
    - nornir-netmiko>=0.1.2
    - Devices must support 'show ip arp' (Cisco IOS/IOS-XE/NX-OS)
    - SSH access with valid credentials configured in Nornir inventory
"""

import argparse
import csv
import json
import logging
import re
import sys
from collections import defaultdict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ARP_PATTERN = re.compile(
    r"^Internet\s+(\d+\.\d+\.\d+\.\d+)\s+\d+\s+([\w.]+)\s+ARPA\s+(\S+)",
    re.MULTILINE,
)


def collect_arp_table(task: Task) -> Result:
    result = task.run(
        task=netmiko_send_command,
        command_string="show ip arp",
    )
    entries = []
    for match in ARP_PATTERN.finditer(result.result):
        ip, mac, interface = match.groups()
        entries.append({
            "device": task.host.name,
            "ip": ip,
            "mac": mac.upper(),
            "interface": interface,
        })
    return Result(host=task.host, result=entries)


def detect_conflicts(all_entries):
    by_ip = defaultdict(list)
    by_mac = defaultdict(list)
    for entry in all_entries:
        by_ip[entry["ip"]].append(entry)
        if entry["mac"] != "INCOMPLETE":
            by_mac[entry["mac"]].append(entry)

    return {
        "duplicate_ips": {
            ip: entries for ip, entries in by_ip.items()
            if len({e["mac"] for e in entries}) > 1
        },
        "duplicate_macs": {
            mac: entries for mac, entries in by_mac.items()
            if len({e["ip"] for e in entries}) > 1
        },
    }


def output_table(entries):
    if not entries:
        print("No ARP entries collected.")
        return
    fmt = f"{'Device':<20} {'IP Address':<18} {'MAC Address':<20} {'Interface':<20}"
    print(fmt)
    print("-" * len(fmt))
    for e in sorted(entries, key=lambda x: (x["device"], x["ip"])):
        print(
            f"{e['device']:<20} {e['ip']:<18} {e['mac']:<20} {e['interface']:<20}"
        )


def output_csv(entries, filepath):
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["device", "ip", "mac", "interface"]
        )
        writer.writeheader()
        writer.writerows(sorted(entries, key=lambda x: (x["device"], x["ip"])))
    logger.info("CSV written to %s (%d entries)", filepath, len(entries))


def print_conflicts(conflicts):
    dup_ips = conflicts["duplicate_ips"]
    dup_macs = conflicts["duplicate_macs"]

    if dup_ips:
        print(f"\n[!] DUPLICATE IPs ({len(dup_ips)} conflict(s)):")
        for ip, entries in sorted(dup_ips.items()):
            macs = sorted({e["mac"] for e in entries})
            devices = sorted({e["device"] for e in entries})
            print(f"    {ip}  MACs: {macs}  Devices: {devices}")

    if dup_macs:
        print(f"\n[!] DUPLICATE MACs ({len(dup_macs)} conflict(s)):")
        for mac, entries in sorted(dup_macs.items()):
            ips = sorted({e["ip"] for e in entries})
            devices = sorted({e["device"] for e in entries})
            print(f"    {mac}  IPs: {ips}  Devices: {devices}")

    if not dup_ips and not dup_macs:
        print("\n[OK] No ARP conflicts detected.")


def main():
    parser = argparse.ArgumentParser(
        description="Collect and analyze ARP tables across network devices."
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Nornir config file (default: config.yaml)",
    )
    parser.add_argument(
        "--filter-group", metavar="GROUP",
        help="Limit collection to hosts in this Nornir group",
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Write results to FILE as CSV",
    )
    parser.add_argument(
        "--format", choices=["table", "csv", "json"], default="table",
        help="Output format when not writing to file (default: table)",
    )
    parser.add_argument(
        "--detect-conflicts", action="store_true",
        help="Report duplicate IPs and MACs across the fleet",
    )
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Parallel worker threads (default: 10)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config)
    except Exception as exc:
        logger.error("Failed to initialize Nornir: %s", exc)
        sys.exit(1)

    nr.config.runner.options["num_workers"] = args.workers

    if args.filter_group:
        nr = nr.filter(F(groups__contains=args.filter_group))
        if not nr.inventory.hosts:
            logger.error("No hosts found in group '%s'", args.filter_group)
            sys.exit(1)

    logger.info(
        "Collecting ARP tables from %d device(s)...", len(nr.inventory.hosts)
    )
    results = nr.run(task=collect_arp_table, name="collect_arp_table")

    all_entries = []
    failed = []
    for host, multi_result in results.items():
        if multi_result.failed:
            failed.append(host)
            logger.warning(
                "Failed on %s: %s", host, multi_result[0].exception
            )
            continue
        entries = multi_result[1].result
        all_entries.extend(entries)
        logger.debug("%s: %d entries", host, len(entries))

    if failed:
        logger.warning(
            "Collection failed on %d device(s): %s",
            len(failed), ", ".join(failed),
        )

    logger.info("Total ARP entries collected: %d", len(all_entries))

    if args.output:
        output_csv(all_entries, args.output)
    elif args.format == "csv":
        writer = csv.DictWriter(
            sys.stdout, fieldnames=["device", "ip", "mac", "interface"]
        )
        writer.writeheader()
        writer.writerows(
            sorted(all_entries, key=lambda x: (x["device"], x["ip"]))
        )
    elif args.format == "json":
        print(json.dumps(all_entries, indent=2))
    else:
        output_table(all_entries)

    if args.detect_conflicts and all_entries:
        print_conflicts(detect_conflicts(all_entries))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```