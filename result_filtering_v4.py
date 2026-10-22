```python
"""
route_filter.py - Nornir-based routing table filter and analyzer.

Collects IP routing tables from network devices and filters entries by protocol,
prefix-length range, and/or next-hop address. Useful for auditing route
proliferation, verifying redistribution policy, and hunting specific prefixes
across a fleet.

Usage:
    python route_filter.py -H 192.168.1.1 -u admin -p secret
    python route_filter.py -H 192.168.1.1 -u admin -p secret --protocol O
    python route_filter.py -H 192.168.1.1 -u admin -p secret --min-prefix 28
    python route_filter.py -H 192.168.1.1 -u admin -p secret --next-hop 10.0.0.1

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Target devices must support "show ip route" (IOS/IOS-XE/NX-OS).
"""

import argparse
import logging
import re
import sys
from typing import Optional

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PROTOCOL_LABELS = {
    "O": "OSPF", "B": "BGP", "S": "Static", "C": "Connected",
    "L": "Local", "R": "RIP", "E": "EIGRP", "i": "ISIS",
}

ROUTE_RE = re.compile(
    r"^(?P<proto>[OBSLCREI*\s]{1,3})\s+"
    r"(?P<prefix>\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})"
    r".*?(?:via\s+(?P<nexthop>\d{1,3}(?:\.\d{1,3}){3}))?",
    re.MULTILINE,
)


def parse_routes(raw: str) -> list[dict]:
    routes = []
    for m in ROUTE_RE.finditer(raw):
        proto_raw = m.group("proto").strip().lstrip("*").strip()
        prefix = m.group("prefix")
        nexthop = m.group("nexthop") or "directly connected"
        try:
            _, length = prefix.split("/")
            prefix_len = int(length)
        except ValueError:
            continue
        routes.append({
            "proto": proto_raw,
            "prefix": prefix,
            "prefix_len": prefix_len,
            "nexthop": nexthop,
        })
    return routes


def filter_routes(
    routes: list[dict],
    protocol: Optional[str],
    min_prefix: Optional[int],
    max_prefix: Optional[int],
    next_hop: Optional[str],
) -> list[dict]:
    out = routes
    if protocol:
        out = [r for r in out if r["proto"].upper().startswith(protocol.upper())]
    if min_prefix is not None:
        out = [r for r in out if r["prefix_len"] >= min_prefix]
    if max_prefix is not None:
        out = [r for r in out if r["prefix_len"] <= max_prefix]
    if next_hop:
        out = [r for r in out if next_hop in r["nexthop"]]
    return out


def collect_and_filter(
    task: Task,
    protocol: Optional[str],
    min_prefix: Optional[int],
    max_prefix: Optional[int],
    next_hop: Optional[str],
) -> Result:
    result = task.run(
        task=netmiko_send_command,
        command_string="show ip route",
        name="show ip route",
    )
    raw = result[0].result or ""
    routes = parse_routes(raw)
    filtered = filter_routes(routes, protocol, min_prefix, max_prefix, next_hop)

    lines = [f"{'PROTO':<8} {'PREFIX':<22} {'NEXT-HOP':<20}"]
    lines.append("-" * 52)
    for r in filtered:
        label = PROTOCOL_LABELS.get(r["proto"], r["proto"])
        lines.append(f"{label:<8} {r['prefix']:<22} {r['nexthop']:<20}")
    lines.append(f"\n{len(filtered)} route(s) matched (of {len(routes)} total)")

    return Result(host=task.host, result="\n".join(lines))


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter routing table entries from network devices via Nornir."
    )
    parser.add_argument("-H", "--host", required=True, help="Device hostname or IP")
    parser.add_argument("-u", "--username", required=True, help="SSH username")
    parser.add_argument("-p", "--password", required=True, help="SSH password")
    parser.add_argument(
        "--device-type", default="cisco_ios",
        help="Netmiko device type (default: cisco_ios)"
    )
    parser.add_argument(
        "--protocol", metavar="PROTO",
        help="Filter by routing protocol letter: O, B, S, C, R, E, i"
    )
    parser.add_argument(
        "--min-prefix", type=int, metavar="LEN",
        help="Minimum prefix length (e.g. 24)"
    )
    parser.add_argument(
        "--max-prefix", type=int, metavar="LEN",
        help="Maximum prefix length (e.g. 32)"
    )
    parser.add_argument(
        "--next-hop", metavar="IP",
        help="Filter routes by next-hop IP (substring match)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = build_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": None,
                "group_file": None,
                "defaults_file": None,
            },
        },
        logging={"enabled": False},
    )

    nr.inventory.hosts[args.host] = nr.inventory.hosts.__class__.__missing__(
        nr.inventory.hosts, args.host
    ) if False else type(
        "Host", (), {}
    )()

    from nornir.core.inventory import Host, Defaults
    nr.inventory.hosts[args.host] = Host(
        name=args.host,
        hostname=args.host,
        username=args.username,
        password=args.password,
        platform=args.device_type,
        defaults=Defaults(),
    )

    result = nr.run(
        task=collect_and_filter,
        protocol=args.protocol,
        min_prefix=args.min_prefix,
        max_prefix=args.max_prefix,
        next_hop=args.next_hop,
    )

    for host, multi in result.items():
        for task_result in multi:
            if task_result.failed:
                logger.error("Task failed on %s: %s", host, task_result.exception)
                sys.exit(1)
            print(f"\n=== {host} ===")
            print(task_result.result)

    if result.failed:
        sys.exit(1)
```