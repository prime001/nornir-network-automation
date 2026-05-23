```python
"""
bgp_route_analysis.py — BGP Routing Table AS-Path Analyzer

Connects to one or more routers via Nornir + Netmiko, retrieves the full BGP
routing table, and analyzes AS-path attributes to surface routing health issues:

  • AS-path loops: same ASN repeated in a path (indicates misconfiguration)
  • Private ASN leaks: ASNs in 64512-65534 or 4200000000-4294967294 ranges
  • Excessive path length: routes exceeding a configurable hop threshold
  • Per-device AS-path length distribution summary

Complements bgp_summary.py (session health) and bgp_summary_v2.py
(prefix-limit utilization) by analyzing the quality of learned routes,
not the session state or capacity.

Usage:
    Single device:
        python bgp_route_analysis.py --host 10.0.0.1 -u admin -p secret

    Inventory file:
        python bgp_route_analysis.py --inventory hosts.yaml -u admin -p secret

    Custom thresholds:
        python bgp_route_analysis.py --host 10.0.0.1 -u admin -p secret \\
            --max-path-len 6 --no-private-asn

    Write CSV of all routes:
        python bgp_route_analysis.py --host 10.0.0.1 -u admin -p secret \\
            --csv routes.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Cisco IOS / IOS-XE default; set --platform for other netmiko device types.
"""

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, TextIO

from nornir import InitNornir
from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)

PRIVATE_ASN_RANGES = [(64512, 65534), (4200000000, 4294967294)]

# Matches best/valid-path lines from "show ip bgp":
# >  10.0.0.0/24   192.0.2.1   0  0  0  65001 65002 i
_ROUTE_RE = re.compile(
    r"^[*si ]{0,2}[>]\s+(?P<prefix>\d+\.\d+\.\d+\.\d+/\d+)\s+\S+\s+"
    r"(?:\d+\s+){3}(?P<aspath>[\d ]*)[ie?]",
    re.MULTILINE,
)


@dataclass
class RouteEntry:
    device: str
    prefix: str
    as_path: List[int]

    @property
    def path_len(self) -> int:
        return len(self.as_path)

    @property
    def has_loop(self) -> bool:
        return len(self.as_path) != len(set(self.as_path))

    @property
    def private_asns(self) -> List[int]:
        found = []
        for asn in self.as_path:
            for lo, hi in PRIVATE_ASN_RANGES:
                if lo <= asn <= hi:
                    found.append(asn)
                    break
        return found


def collect_bgp_routes(task: Task) -> Result:
    raw = task.run(
        task=netmiko_send_command,
        command_string="show ip bgp",
        name="show_ip_bgp",
    ).result

    routes: List[RouteEntry] = []
    for m in _ROUTE_RE.finditer(raw):
        path_str = m.group("aspath").strip()
        as_path = [int(a) for a in path_str.split()] if path_str else []
        routes.append(RouteEntry(
            device=task.host.name,
            prefix=m.group("prefix"),
            as_path=as_path,
        ))

    logger.info("%s: parsed %d best-path BGP routes", task.host, len(routes))
    return Result(host=task.host, result=routes)


def print_report(
    all_routes: List[RouteEntry],
    max_path_len: int,
    check_private: bool,
    fh: TextIO,
) -> int:
    by_device: dict = {}
    for r in all_routes:
        by_device.setdefault(r.device, []).append(r)

    fh.write("\n=== BGP AS-Path Analysis ===\n")
    for device, routes in sorted(by_device.items()):
        lengths = [r.path_len for r in routes]
        avg = sum(lengths) / len(lengths) if lengths else 0
        loops = sum(1 for r in routes if r.has_loop)
        privates = sum(1 for r in routes if r.private_asns)
        fh.write(
            f"\n{device}: {len(routes)} routes | "
            f"avg path {avg:.1f} | max path {max(lengths, default=0)} | "
            f"{loops} loop(s) | {privates} private-ASN route(s)\n"
        )

    flagged = [
        r for r in all_routes
        if r.has_loop
        or r.path_len > max_path_len
        or (check_private and r.private_asns)
    ]

    if not flagged:
        fh.write("\nNo flagged routes.\n")
        return 0

    col = "{:<20} {:<22} {:>7}  {}"
    fh.write(f"\n{col.format('Device', 'Prefix', 'PathLen', 'Flags')}\n")
    fh.write("-" * 68 + "\n")
    for r in sorted(flagged, key=lambda x: (x.device, -x.path_len, x.prefix)):
        flags = []
        if r.has_loop:
            flags.append("LOOP")
        if r.path_len > max_path_len:
            flags.append(f"LONG>{max_path_len}")
        if check_private and r.private_asns:
            flags.append(f"PRIVATE({','.join(str(a) for a in r.private_asns)})")
        fh.write(col.format(r.device, r.prefix, r.path_len, " ".join(flags)) + "\n")

    fh.write(f"\n{len(flagged)} flagged route(s).\n")
    return len(flagged)


def write_csv(routes: List[RouteEntry], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["device", "prefix", "path_len", "has_loop", "private_asns", "as_path"])
        for r in sorted(routes, key=lambda x: (x.device, x.prefix)):
            w.writerow([
                r.device, r.prefix, r.path_len, r.has_loop,
                ";".join(str(a) for a in r.private_asns),
                " ".join(str(a) for a in r.as_path),
            ])
    logger.info("CSV written to %s", path)


def _build_single_host_nr(host: str, username: str, password: str, platform: str):
    conn_opts = ConnectionOptions(extras={"device_type": platform})
    hosts = Hosts({
        host: Host(
            name=host, hostname=host,
            username=username, password=password,
            connection_options={"netmiko": conn_opts},
        )
    })
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
        logging={"enabled": False},
        inventory={"plugin": "SimpleInventory"},
        _hosts=hosts, _groups=Groups(), _defaults=Defaults(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BGP routing table AS-path analyzer — loops, private ASNs, long paths"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--host", help="Single device hostname or IP")
    src.add_argument("--inventory", metavar="FILE", help="Nornir SimpleInventory hosts.yaml")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument(
        "--platform", default="cisco_ios", metavar="TYPE",
        help="Netmiko device type (default: cisco_ios)",
    )
    parser.add_argument(
        "--max-path-len", type=int, default=8, metavar="N",
        help="Flag routes with AS-path longer than N hops (default: 8)",
    )
    parser.add_argument(
        "--no-private-asn", action="store_true",
        help="Disable private ASN leak detection",
    )
    parser.add_argument("--csv", metavar="FILE", help="Write full route table to CSV")
    parser.add_argument("--output", metavar="FILE", help="Write report to file (default: stdout)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if args.host:
        nr = _build_single_host_nr(args.host, args.username, args.password, args.platform)
    else:
        nr = InitNornir(
            config_file=args.inventory,
            runner={"plugin": "threaded", "options": {"num_workers": 10}},
            logging={"enabled": False},
        )
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password

    agg_results = nr.run(task=collect_bgp_routes, name="bgp_route_analysis")

    all_routes: List[RouteEntry] = []
    for host_name, multi in agg_results.items():
        if multi.failed:
            print(f"ERROR  {host_name}: {multi[0].exception}", file=sys.stderr)
        else:
            all_routes.extend(multi[0].result)

    if not all_routes:
        print("No routes collected.", file=sys.stderr)
        return 1

    if args.csv:
        write_csv(all_routes, args.csv)

    fh: Optional[TextIO] = None
    try:
        fh = open(args.output, "w") if args.output else sys.stdout
        flagged = print_report(all_routes, args.max_path_len, not args.no_private_asn, fh)
    finally:
        if fh and args.output:
            fh.close()

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
```