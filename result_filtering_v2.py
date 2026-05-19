```python
"""
ARP Table Cross-Device Filter — nornir-network-automation

Collects ARP tables from multiple devices via Nornir/Netmiko and filters
results by IP subnet, MAC OUI prefix, interface, or duplicate IP detection.
Useful for host location, duplicate IP auditing, and vendor-specific MAC
hunting across an entire site.

Usage:
    python arp_table_filter.py --subnet 10.10.0.0/24
    python arp_table_filter.py --mac-prefix 00:1a:2b
    python arp_table_filter.py --interface vlan
    python arp_table_filter.py --duplicate-ips
    python arp_table_filter.py --hosts r1,r2 --subnet 192.168.0.0/16

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    nornir.yaml with hosts.yaml and groups.yaml inventory configured
"""

import argparse
import ipaddress
import logging
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Matches Cisco IOS/IOS-XE ARP output lines
_ARP_LINE = re.compile(
    r"(?:Internet)\s+"
    r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(?P<age>[\d\-]+)\s+"
    r"(?P<mac>[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\s+"
    r"\S+\s+"
    r"(?P<iface>\S+)"
)


def _dotted_to_colon(mac: str) -> str:
    """Convert Cisco aabb.ccdd.eeff notation to 00:aa:bb:cc:dd:ee."""
    raw = mac.replace(".", "")
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).lower()


def collect_arp_table(task: Task) -> Result:
    """Nornir task: run 'show ip arp' and parse entries into a list of dicts."""
    cmd_result = task.run(
        task=netmiko_send_command,
        command_string="show ip arp",
    )
    entries = []
    for line in cmd_result.result.splitlines():
        match = _ARP_LINE.search(line)
        if match:
            entries.append({
                "ip": match.group("ip"),
                "mac": _dotted_to_colon(match.group("mac")),
                "interface": match.group("iface"),
                "age": match.group("age"),
            })
    return Result(host=task.host, result=entries)


def apply_filters(
    entries: List[dict],
    subnet: Optional[str],
    mac_prefix: Optional[str],
    interface: Optional[str],
) -> List[dict]:
    result = entries

    if subnet:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError as exc:
            logger.error("Invalid subnet '%s': %s", subnet, exc)
            sys.exit(1)
        result = [e for e in result if ipaddress.ip_address(e["ip"]) in network]

    if mac_prefix:
        # Accept 00:1a:2b, 001a2b, or 00-1a-2b forms
        normalized = mac_prefix.lower().replace("-", ":").replace(".", "")
        if len(normalized) == 6 and ":" not in normalized:
            normalized = ":".join(normalized[i:i + 2] for i in range(0, 6, 2))
        result = [e for e in result if e["mac"].startswith(normalized)]

    if interface:
        result = [e for e in result if interface.lower() in e["interface"].lower()]

    return result


def find_duplicate_ips(all_entries: Dict[str, List[dict]]) -> Dict[str, List[str]]:
    ip_to_hosts: Dict[str, List[str]] = defaultdict(list)
    for host, entries in all_entries.items():
        for entry in entries:
            ip_to_hosts[entry["ip"]].append(host)
    return {ip: hosts for ip, hosts in ip_to_hosts.items() if len(hosts) > 1}


def print_table(host: str, entries: List[dict]) -> None:
    print(f"\n{'=' * 62}")
    print(f"  {host}  ({len(entries)} entries)")
    print(f"{'=' * 62}")
    print(f"  {'IP':<18} {'MAC':<20} {'Interface':<20} Age")
    print(f"  {'-' * 17} {'-' * 19} {'-' * 19} ---")
    for e in sorted(entries, key=lambda x: tuple(int(o) for o in x["ip"].split("."))):
        print(f"  {e['ip']:<18} {e['mac']:<20} {e['interface']:<20} {e['age']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter ARP tables across Nornir-managed devices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="nornir.yaml", help="Nornir config file")
    parser.add_argument("--subnet", metavar="CIDR", help="Filter by IP subnet (e.g. 10.0.0.0/24)")
    parser.add_argument("--mac-prefix", metavar="OUI", help="Filter by MAC prefix (e.g. 00:1a:2b)")
    parser.add_argument("--interface", metavar="NAME", help="Filter by interface name substring")
    parser.add_argument(
        "--duplicate-ips", action="store_true",
        help="Report IPs that appear in ARP tables on more than one device",
    )
    parser.add_argument(
        "--hosts", metavar="H1,H2",
        help="Comma-separated list of hostnames to target (default: all)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config)
    except Exception as exc:
        logger.error("Failed to initialize Nornir: %s", exc)
        sys.exit(1)

    if args.hosts:
        target = {h.strip() for h in args.hosts.split(",")}
        nr = nr.filter(lambda h: h.name in target)

    if not nr.inventory.hosts:
        logger.error("No hosts matched the given filter.")
        sys.exit(1)

    logger.info("Collecting ARP tables from %d host(s).", len(nr.inventory.hosts))
    aggregated = nr.run(task=collect_arp_table)

    all_entries: Dict[str, List[dict]] = {}
    for host, multi in aggregated.items():
        if multi.failed:
            logger.warning("%-20s  FAILED: %s", host, multi[0].exception)
            continue
        all_entries[host] = multi[0].result

    if args.duplicate_ips:
        dupes = find_duplicate_ips(all_entries)
        if not dupes:
            print("No duplicate IPs found across collected ARP tables.")
            return
        print(f"\nDuplicate IPs ({len(dupes)} found):")
        for ip in sorted(dupes, key=lambda x: tuple(int(o) for o in x.split("."))):
            print(f"  {ip:<18}  seen on: {', '.join(sorted(dupes[ip]))}")
        return

    found_any = False
    for host in sorted(all_entries):
        filtered = apply_filters(
            all_entries[host], args.subnet, args.mac_prefix, args.interface
        )
        if filtered:
            found_any = True
            print_table(host, filtered)

    if not found_any:
        print("No matching ARP entries found.")


if __name__ == "__main__":
    main()
```