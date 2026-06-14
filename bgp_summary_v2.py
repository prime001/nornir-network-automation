The prompt asks me to output only the script content. Based on the existing bgp_summary.py and bgp_summary_v2.py scripts already in the repo, I'll write a BGP neighbor health monitor with multi-VRF support and health scoring — a distinct operational angle.

```python
"""
BGP Neighbor Health Monitor — multi-VRF uptime, prefix-count, and state analysis.

Purpose:
    Connects to one or more network devices via Nornir/Netmiko and parses
    'show bgp all summary' (or per-VRF variants) to produce a health report
    that flags neighbors in non-Established state, low uptime, or prefix-count
    anomalies.  Distinct from bgp_summary_v2 which reports raw table data;
    this script scores each neighbor and exits non-zero when any are degraded.

Usage:
    python 021_bgp_neighbor_health.py --host 192.0.2.1 --user admin \\
        --password secret [--vrf all] [--prefix-warn 500] [--uptime-warn 300]
        [--json]

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
"""

import argparse
import json
import logging
import re
import sys

from nornir import InitNornir
from nornir.core.inventory import Host, Group, Defaults
from nornir.core.inventory import Inventory
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

UPTIME_RE = re.compile(
    r"(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?|(\d+:\d+:\d+)"
)


def parse_uptime_seconds(uptime_str: str) -> int:
    """Convert IOS uptime string (e.g. '2w3d', '00:04:12') to seconds."""
    m = re.match(r"^(\d+):(\d+):(\d+)$", uptime_str.strip())
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mn * 60 + s
    total = 0
    for unit, mult in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        hit = re.search(rf"(\d+){unit}", uptime_str)
        if hit:
            total += int(hit.group(1)) * mult
    return total


def parse_bgp_summary(raw: str) -> list:
    """Extract neighbor rows from IOS/IOS-XE 'show bgp summary' output."""
    neighbors = []
    for line in raw.splitlines():
        m = re.match(
            r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+\d+\s+(\d+)\s+\d+\s+\d+\s+"
            r"\d+\s+\d+\s+(\S+)\s+(\d+)$",
            line.strip(),
        )
        if m:
            ip, asn, uptime, prefixes = m.group(1), m.group(2), m.group(3), m.group(4)
            neighbors.append(
                {
                    "neighbor": ip,
                    "remote_as": int(asn),
                    "uptime_raw": uptime,
                    "uptime_seconds": parse_uptime_seconds(uptime),
                    "prefixes_received": int(prefixes),
                    "state": "Established" if uptime[0].isdigit() or ":" in uptime else uptime,
                }
            )
    return neighbors


def assess_health(neighbor: dict, prefix_warn: int, uptime_warn: int) -> str:
    if neighbor["state"] != "Established":
        return "DOWN"
    if neighbor["uptime_seconds"] < uptime_warn:
        return "FLAPPING"
    if neighbor["prefixes_received"] > prefix_warn:
        return "PREFIX_WARN"
    return "OK"


def bgp_health_task(task, vrf: str, prefix_warn: int, uptime_warn: int):
    cmd = "show bgp all summary" if vrf == "all" else f"show bgp {vrf} summary"
    result = task.run(task=netmiko_send_command, command_string=cmd)
    raw = result[0].result or ""
    neighbors = parse_bgp_summary(raw)
    for n in neighbors:
        n["health"] = assess_health(n, prefix_warn, uptime_warn)
        n["device"] = task.host.name
    task.host["bgp_neighbors"] = neighbors


def build_nornir(host: str, username: str, password: str, platform: str) -> object:
    inv = Inventory(
        hosts={host: Host(name=host, hostname=host, username=username,
                          password=password, platform=platform)},
        groups={},
        defaults=Defaults(),
    )
    return InitNornir(inventory={"plugin": "SimpleInventory"}, runner={"plugin": "threaded", "options": {"num_workers": 1}}, logging={"enabled": False}, _inventory=inv)


def main():
    parser = argparse.ArgumentParser(description="BGP neighbor health monitor")
    parser.add_argument("--host", required=True, help="Device hostname or IP")
    parser.add_argument("--user", required=True, help="SSH username")
    parser.add_argument("--password", required=True, help="SSH password")
    parser.add_argument("--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)")
    parser.add_argument("--vrf", default="all", help="VRF name or 'all' (default: all)")
    parser.add_argument("--prefix-warn", type=int, default=500, dest="prefix_warn",
                        help="Warn if prefixes received exceed this (default: 500)")
    parser.add_argument("--uptime-warn", type=int, default=300, dest="uptime_warn",
                        help="Warn if neighbor uptime < N seconds (default: 300)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit JSON output instead of table")
    args = parser.parse_args()

    nr = build_nornir(args.host, args.user, args.password, args.platform)
    nr.run(task=bgp_health_task, vrf=args.vrf,
           prefix_warn=args.prefix_warn, uptime_warn=args.uptime_warn)

    all_neighbors = []
    for name, host in nr.inventory.hosts.items():
        all_neighbors.extend(host.get("bgp_neighbors", []))

    if not all_neighbors:
        log.warning("No BGP neighbors parsed from device output.")
        sys.exit(2)

    if args.as_json:
        print(json.dumps(all_neighbors, indent=2))
    else:
        header = f"{'Neighbor':<18} {'AS':>7} {'State':<12} {'Uptime':<12} {'Pfx':>6} {'Health':<12}"
        print(header)
        print("-" * len(header))
        for n in all_neighbors:
            print(f"{n['neighbor']:<18} {n['remote_as']:>7} {n['state']:<12} "
                  f"{n['uptime_raw']:<12} {n['prefixes_received']:>6} {n['health']:<12}")

    degraded = [n for n in all_neighbors if n["health"] != "OK"]
    if degraded:
        print(f"\n[WARN] {len(degraded)} neighbor(s) degraded:", file=sys.stderr)
        for n in degraded:
            print(f"  {n['neighbor']} -> {n['health']}", file=sys.stderr)
        sys.exit(1)

    print(f"\nAll {len(all_neighbors)} neighbor(s) healthy.")


if __name__ == "__main__":
    main()
```