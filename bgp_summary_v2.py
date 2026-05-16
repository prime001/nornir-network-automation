The nornir repo content is in the user's prompt — I have what I need. Writing the script now, targeting BGP prefix-limit utilization monitoring as a non-duplicate angle.

"""
BGP Prefix Limit Monitor

Queries routers for per-neighbor BGP prefix counts and configured maximum-prefix
limits, then reports utilization percentages and flags sessions approaching or
exceeding a configurable warning threshold.

Unlike bgp_summary.py (session health) and bgp_summary_v2.py (per-VRF reporting),
this script answers the capacity-planning question: how close is each BGP session
to its configured prefix ceiling?

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils

Usage:
    Single host:
        python bgp_prefix_monitor.py --host 192.0.2.1 -u admin -p secret

    Multiple hosts via YAML inventory:
        python bgp_prefix_monitor.py --inventory hosts.yaml -u admin -p secret

    Custom warning threshold (default 75 %):
        python bgp_prefix_monitor.py --host 192.0.2.1 -u admin -p secret \
            --threshold 80

    Write report to file:
        python bgp_prefix_monitor.py --host 192.0.2.1 -u admin -p secret \
            --output report.txt
"""

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TextIO

from nornir import InitNornir
from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("bgp_prefix_monitor")

_SUMMARY_RE = re.compile(
    r"^(?P<peer>\d+\.\d+\.\d+\.\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+"
    r"\S+\s+(?P<state_or_pfx>\S+)",
    re.MULTILINE,
)
_MAX_PREFIX_RE = re.compile(
    r"Maximum prefixes allowed\s+(?P<limit>\d+)"
    r"(?:\s+\((?P<warn_pct>\d+)%\))?"
)


@dataclass
class NeighborLimit:
    peer: str
    received: int
    prefix_limit: Optional[int] = None
    device_warn_pct: int = 75

    @property
    def utilization(self) -> Optional[float]:
        if self.prefix_limit and self.prefix_limit > 0:
            return (self.received / self.prefix_limit) * 100.0
        return None

    @property
    def status(self) -> str:
        util = self.utilization
        if util is None:
            return "NO_LIMIT"
        if util >= 100.0:
            return "CRITICAL"
        if util >= self.device_warn_pct:
            return "WARNING"
        return "OK"


def collect_bgp_prefix_data(task: Task) -> Result:
    summary_out = task.run(
        task=netmiko_send_command,
        command_string="show ip bgp summary",
        name="bgp_summary",
    ).result

    established: List[NeighborLimit] = []
    for m in _SUMMARY_RE.finditer(summary_out):
        peer = m.group("peer")
        state_or_pfx = m.group("state_or_pfx")
        if not state_or_pfx.isdigit():
            logger.debug("%s: peer %s not established (%s), skipping", task.host, peer, state_or_pfx)
            continue
        established.append(NeighborLimit(peer=peer, received=int(state_or_pfx)))

    for nbr in established:
        detail_out = task.run(
            task=netmiko_send_command,
            command_string=f"show ip bgp neighbors {nbr.peer}",
            name=f"bgp_neighbor_{nbr.peer}",
        ).result

        m = _MAX_PREFIX_RE.search(detail_out)
        if m:
            nbr.prefix_limit = int(m.group("limit"))
            if m.group("warn_pct"):
                nbr.device_warn_pct = int(m.group("warn_pct"))

    return Result(host=task.host, result=established)


def print_report(
    device_results: Dict[str, List[NeighborLimit]],
    threshold: int,
    fh: TextIO,
) -> int:
    col = "{:<20} {:<18} {:>10} {:>12} {:>9}  {}"
    header = col.format("Device", "Peer", "Received", "MaxPrefix", "Util%", "Status")
    separator = "-" * len(header)
    fh.write(f"\n{header}\n{separator}\n")

    flagged = 0
    for device, neighbors in sorted(device_results.items()):
        for nbr in sorted(neighbors, key=lambda n: n.peer):
            util_str = f"{nbr.utilization:.1f}" if nbr.utilization is not None else "n/a"
            limit_str = str(nbr.prefix_limit) if nbr.prefix_limit else "none"
            status = nbr.status
            if nbr.utilization is not None and nbr.utilization >= threshold:
                flagged += 1
            fh.write(
                col.format(device, nbr.peer, nbr.received, limit_str, util_str, status)
                + "\n"
            )

    fh.write(f"\n{flagged} neighbor(s) at or above {threshold}% utilization.\n")
    return flagged


def build_nornir_from_host(host: str, username: str, password: str, platform: str):
    conn_opts = ConnectionOptions(extras={"device_type": platform})
    hosts = Hosts(
        {
            host: Host(
                name=host,
                hostname=host,
                username=username,
                password=password,
                connection_options={"netmiko": conn_opts},
            )
        }
    )
    return InitNornir(
        inventory={"plugin": "SimpleInventory"},
        runner={"plugin": "threaded", "options": {"num_workers": 10}},
        logging={"enabled": False},
        core={"raise_on_error": False},
        _hosts=hosts,
        _groups=Groups(),
        _defaults=Defaults(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="BGP prefix-limit utilization monitor")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--host", help="Single device hostname or IP")
    src.add_argument("--inventory", help="Path to Nornir SimpleInventory hosts.yaml")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument("--platform", default="cisco_ios", help="Netmiko platform type")
    parser.add_argument(
        "--threshold",
        type=int,
        default=75,
        help="Utilization %% at which to flag a neighbor (default: 75)",
    )
    parser.add_argument("--output", help="Write report to this file path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("bgp_prefix_monitor").setLevel(logging.DEBUG)

    if args.host:
        nr = build_nornir_from_host(args.host, args.username, args.password, args.platform)
    else:
        nr = InitNornir(
            config_file=args.inventory,
            runner={"plugin": "threaded", "options": {"num_workers": 10}},
            logging={"enabled": False},
        )
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password

    results = nr.run(task=collect_bgp_prefix_data, name="bgp_prefix_monitor")

    device_data: Dict[str, List[NeighborLimit]] = {}
    for host_name, multi_result in results.items():
        if multi_result.failed:
            logger.error("Failed to collect data from %s: %s", host_name, multi_result[0].exception)
            continue
        device_data[host_name] = multi_result[0].result

    fh = open(args.output, "w") if args.output else sys.stdout
    try:
        flagged = print_report(device_data, args.threshold, fh)
    finally:
        if args.output:
            fh.close()

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())