Route Table Filter - Nornir-based routing table collector and filter.

Collects IP routing tables from network devices and filters results by
routing protocol, destination prefix (with subnet overlap detection), or
next-hop address. Useful for quickly verifying route propagation across
a fleet without logging into each device individually.

Usage:
    python 008_route_filter.py --protocol ospf
    python 008_route_filter.py --prefix 10.0.0.0/8 --hosts router1,router2
    python 008_route_filter.py --next-hop 192.168.1.1 --group core
    python 008_route_filter.py --protocol bgp --prefix 0.0.0.0/0

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: hosts.yaml, groups.yaml, defaults.yaml (or config.yaml)
    Devices must support "show ip route" (IOS, IOS-XE, IOS-XR, NX-OS).
"""

import argparse
import ipaddress
import logging
import re
import sys
from typing import Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROTOCOL_CODES = {
    "ospf": ["O", "O IA", "O E1", "O E2", "O N1", "O N2"],
    "bgp": ["B"],
    "connected": ["C"],
    "static": ["S", "S*"],
    "eigrp": ["D", "D EX"],
    "rip": ["R"],
    "isis": ["i", "i L1", "i L2"],
}

_ROUTE_RE = re.compile(
    r"^[ \t]*([A-Za-z*][ A-Za-z*]{0,5})"
    r"[ \t]+(\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?)"
    r"(?:[^\n]*?via[ \t]+(\d{1,3}(?:\.\d{1,3}){3}))?",
    re.MULTILINE,
)


def parse_routes(raw: str) -> list:
    routes = []
    for m in _ROUTE_RE.finditer(raw):
        proto = m.group(1).strip()
        network = m.group(2)
        next_hop = m.group(3) or ""
        if proto and network:
            routes.append({"protocol": proto, "network": network, "next_hop": next_hop})
    return routes


def collect_routes(task: Task) -> Result:
    r = task.run(task=netmiko_send_command, command_string="show ip route")
    return Result(host=task.host, result=parse_routes(r.result))


def _overlaps(network: str, target: str) -> bool:
    try:
        return ipaddress.ip_network(network, strict=False).overlaps(
            ipaddress.ip_network(target, strict=False)
        )
    except ValueError:
        return False


def filter_routes(
    routes: list,
    protocol: Optional[str],
    prefix: Optional[str],
    next_hop: Optional[str],
) -> list:
    out = routes

    if protocol:
        codes = PROTOCOL_CODES.get(protocol.lower(), [protocol.upper()])
        out = [r for r in out if any(r["protocol"].startswith(c) for c in codes)]

    if prefix:
        out = [r for r in out if _overlaps(r["network"], prefix)]

    if next_hop:
        out = [r for r in out if next_hop in r["next_hop"]]

    return out


def _print_table(host: str, routes: list) -> None:
    print(f"[{host}] {len(routes)} route(s) matched:")
    print(f"  {'Proto':<10} {'Network':<22} {'Next-Hop'}")
    print(f"  {'-'*10} {'-'*22} {'-'*16}")
    for r in routes:
        print(f"  {r['protocol']:<10} {r['network']:<22} {r['next_hop'] or '(direct)'}")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect and filter IP routing tables across network devices."
    )
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--hosts", help="Comma-separated hostnames to target")
    p.add_argument("--group", help="Limit to a named inventory group")
    p.add_argument(
        "--protocol",
        choices=list(PROTOCOL_CODES.keys()),
        metavar="PROTO",
        help=f"Filter by protocol: {', '.join(PROTOCOL_CODES.keys())}",
    )
    p.add_argument("--prefix", metavar="NET/LEN", help="Filter by destination prefix overlap")
    p.add_argument("--next-hop", dest="next_hop", metavar="IP", help="Filter by next-hop address")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not any([args.protocol, args.prefix, args.next_hop]):
        print("error: specify at least one filter: --protocol, --prefix, or --next-hop",
              file=sys.stderr)
        sys.exit(1)

    if args.prefix:
        try:
            ipaddress.ip_network(args.prefix, strict=False)
        except ValueError as exc:
            print(f"error: invalid prefix '{args.prefix}': {exc}", file=sys.stderr)
            sys.exit(1)

    try:
        nr = InitNornir(config_file=args.config)
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        sys.exit(1)

    if args.hosts:
        wanted = set(args.hosts.split(","))
        nr = nr.filter(filter_func=lambda h: h.name in wanted)
    elif args.group:
        group = args.group
        nr = nr.filter(filter_func=lambda h: group in h.groups)

    if not nr.inventory.hosts:
        print("No hosts matched.", file=sys.stderr)
        sys.exit(1)

    print(f"Querying {len(nr.inventory.hosts)} device(s)...\n")
    results = nr.run(task=collect_routes)

    any_found = False
    for host, multi in results.items():
        if multi.failed:
            print(f"[{host}] FAILED: {multi.exception}\n")
            continue
        matched = filter_routes(multi[0].result, args.protocol, args.prefix, args.next_hop)
        if not matched:
            print(f"[{host}] No matching routes.\n")
        else:
            any_found = True
            _print_table(host, matched)

    if not any_found:
        print("No matching routes found across any queried device.")
        sys.exit(2)


if __name__ == "__main__":
    main()