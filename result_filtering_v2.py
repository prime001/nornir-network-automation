```python
"""
Route Table Analyzer - Multi-device routing table collection and filtering.

Purpose:
    Collect routing tables from network devices via Nornir and filter
    results by protocol, prefix pattern, next-hop address, or administrative
    distance. Useful for verifying route propagation and auditing routing
    policy across a fleet without logging into devices individually.

Usage:
    python route_table_analyzer.py [options]

    python route_table_analyzer.py --protocol ospf --output table
    python route_table_analyzer.py --prefix 10.0. --next-hop 192.168.1.1
    python route_table_analyzer.py --groups core_routers --min-ad 20 --output csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: hosts.yaml, groups.yaml, defaults.yaml
"""

import argparse
import csv
import json
import logging
import re
import sys

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROTO_MAP = {
    "C": "connected", "S": "static", "O": "ospf", "B": "bgp",
    "E": "eigrp", "R": "rip", "i": "isis", "L": "local",
}

_ROUTE_RE = re.compile(
    r"^(?P<proto>[A-Za-z*]+)\s+"
    r"(?P<network>\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})"
    r"(?:.*?\[(?P<ad>\d+)/(?P<metric>\d+)\])?"
    r"(?:.*?via\s+(?P<nexthop>\d{1,3}(?:\.\d{1,3}){3}))?",
    re.MULTILINE,
)


def collect_routes(task: Task) -> Result:
    r = task.run(task=netmiko_send_command, command_string="show ip route",
                 use_textfsm=True)
    return Result(host=task.host, result=r.result)


def parse_routes(raw):
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return [
            {
                "network": e.get("network", "") + "/" + e.get("mask", ""),
                "protocol": e.get("protocol", "").lower(),
                "next_hop": e.get("nexthop_ip", ""),
                "ad": int(e.get("distance", 0) or 0),
                "metric": int(e.get("metric", 0) or 0),
            }
            for e in raw
        ]
    routes = []
    for m in _ROUTE_RE.finditer(str(raw)):
        code = m.group("proto").lstrip("*").strip()[0]
        routes.append({
            "network": m.group("network"),
            "protocol": PROTO_MAP.get(code, code.lower()),
            "next_hop": m.group("nexthop") or "",
            "ad": int(m.group("ad")) if m.group("ad") else 0,
            "metric": int(m.group("metric")) if m.group("metric") else 0,
        })
    return routes


def filter_routes(routes, protocol=None, prefix=None, next_hop=None, min_ad=None):
    out = routes
    if protocol:
        out = [r for r in out if protocol.lower() in r["protocol"]]
    if prefix:
        out = [r for r in out if r["network"].startswith(prefix)]
    if next_hop:
        out = [r for r in out if r["next_hop"] == next_hop]
    if min_ad is not None:
        out = [r for r in out if r["ad"] >= min_ad]
    return out


def render_table(all_results):
    hdr = f"{'Host':<20} {'Network':<22} {'Protocol':<12} {'Next-Hop':<16} {'AD':>4} {'Metric':>7}"
    print(hdr)
    print("-" * len(hdr))
    for host, routes in sorted(all_results.items()):
        for r in routes:
            print(
                f"{host:<20} {r['network']:<22} {r['protocol']:<12}"
                f" {r['next_hop']:<16} {r['ad']:>4} {r['metric']:>7}"
            )


def render_csv(all_results):
    w = csv.DictWriter(sys.stdout,
                       fieldnames=["host", "network", "protocol", "next_hop", "ad", "metric"],
                       extrasaction="ignore")
    w.writeheader()
    for host, routes in sorted(all_results.items()):
        for r in routes:
            w.writerow({"host": host, **r})


def build_parser():
    p = argparse.ArgumentParser(
        description="Collect and filter routing tables across Nornir-managed devices."
    )
    p.add_argument("--hosts", nargs="+", help="Target specific hostnames")
    p.add_argument("--groups", nargs="+", help="Target inventory groups")
    p.add_argument("--protocol", help="Filter by protocol (ospf, bgp, static, ...)")
    p.add_argument("--prefix", help="Filter networks starting with PREFIX (e.g. 10.0.)")
    p.add_argument("--next-hop", dest="next_hop", help="Filter by next-hop IP")
    p.add_argument("--min-ad", dest="min_ad", type=int,
                   help="Only show routes with AD >= this value")
    p.add_argument("--output", choices=["table", "csv", "json"], default="table")
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    return p


def main():
    args = build_parser().parse_args()

    try:
        nr = InitNornir(config_file=args.config)
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        sys.exit(1)

    if args.hosts:
        nr = nr.filter(F(name__in=args.hosts))
    if args.groups:
        nr = nr.filter(F(groups__any=args.groups))

    if not nr.inventory.hosts:
        print("No hosts matched filters.", file=sys.stderr)
        sys.exit(1)

    results = nr.run(task=collect_routes, name="collect_routes")

    all_results = {}
    for host, multi in results.items():
        if multi.failed:
            logger.warning("Host %s failed: %s", host, multi.exception)
            continue
        routes = parse_routes(multi[0].result if multi else [])
        routes = filter_routes(routes,
                               protocol=args.protocol,
                               prefix=args.prefix,
                               next_hop=args.next_hop,
                               min_ad=args.min_ad)
        if routes:
            all_results[host] = routes

    if not all_results:
        print("No routes matched the specified filters.")
        sys.exit(0)

    if args.output == "table":
        render_table(all_results)
    elif args.output == "csv":
        render_csv(all_results)
    else:
        print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
```