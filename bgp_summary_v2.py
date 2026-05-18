Now I have a clear picture. `bgp_summary.py` is a NAPALM tabular display; `bgp_summary_v2.py` is actually DNS/NTP. The gap is a **prefix-limit utilization monitor** — checks received prefixes against configured max-prefix thresholds and alerts before sessions drop. That's operationally critical and distinct from what's there.

```python
"""
BGP Prefix-Limit Utilization Monitor

Purpose:
    Collect BGP neighbor state and compare received-prefix counts against
    configured max-prefix limits on each session.  Sessions approaching or
    exceeding their limit will drop silently on many platforms, causing
    hard-to-diagnose outages.  This script surfaces utilization as a
    percentage and groups neighbors into CRITICAL / WARNING / OK tiers so
    NOC engineers can act before the limit is hit.

Usage:
    python bgp_prefix_limits.py --hosts inventory/hosts.yaml \
        --groups inventory/groups.yaml --defaults inventory/defaults.yaml
    python bgp_prefix_limits.py --filter role=edge --warn 75 --crit 90
    python bgp_prefix_limits.py --filter site=nyc --json

Prerequisites:
    pip install nornir nornir-netmiko netmiko

    Inventory defaults.yaml must contain username, password, and platform
    (cisco_ios, cisco_xe, cisco_xr, cisco_nxos, or junos).

    Cisco: "show bgp neighbors" is parsed for MaxPfxWarn / Max prefix.
    Junos: "show bgp neighbor" is parsed for prefix-limit fields.
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)


@dataclass
class PeerLimitData:
    host: str
    peer: str
    vrf: str = "default"
    remote_as: int = 0
    state: str = "unknown"
    received_prefixes: int = 0
    max_prefix_limit: Optional[int] = None
    utilization_pct: Optional[float] = None
    severity: str = "UNKNOWN"
    raw_error: str = ""


def _parse_cisco_neighbors(output: str, hostname: str) -> list[PeerLimitData]:
    peers: list[PeerLimitData] = []
    current: dict = {}

    for line in output.splitlines():
        m = re.match(r"^BGP neighbor is (\S+),\s+remote AS (\d+)", line)
        if m:
            if current:
                peers.append(_build_cisco_peer(current, hostname))
            current = {"peer": m.group(1), "remote_as": int(m.group(2)),
                       "state": "unknown", "rcv": 0, "max": None, "vrf": "default"}
            continue

        if not current:
            continue

        m = re.search(r"BGP state = (\w+)", line)
        if m:
            current["state"] = m.group(1)

        m = re.search(r"Prefixes Current:\s+(\d+)", line, re.IGNORECASE)
        if m:
            current["rcv"] = int(m.group(1))

        m = re.search(r"Maximum prefixes:\s+(\d+)", line, re.IGNORECASE)
        if m:
            current["max"] = int(m.group(1))

        m = re.search(r"for address family:\s+\S+\s+in VRF\s+(\S+)", line)
        if m:
            current["vrf"] = m.group(1)

    if current:
        peers.append(_build_cisco_peer(current, hostname))

    return peers


def _build_cisco_peer(c: dict, hostname: str) -> PeerLimitData:
    return PeerLimitData(
        host=hostname,
        peer=c["peer"],
        vrf=c.get("vrf", "default"),
        remote_as=c.get("remote_as", 0),
        state=c.get("state", "unknown"),
        received_prefixes=c.get("rcv", 0),
        max_prefix_limit=c.get("max"),
    )


def _parse_junos_neighbors(output: str, hostname: str) -> list[PeerLimitData]:
    peers: list[PeerLimitData] = []
    current: dict = {}

    for line in output.splitlines():
        m = re.match(r"^Peer:\s+(\S+)\s+AS\s+(\d+)", line)
        if m:
            if current:
                peers.append(_build_junos_peer(current, hostname))
            current = {"peer": m.group(1), "remote_as": int(m.group(2)),
                       "state": "unknown", "rcv": 0, "max": None, "vrf": "master"}

        if not current:
            continue

        m = re.search(r"Type:\s+\S+\s+State:\s+(\w+)", line)
        if m:
            current["state"] = m.group(1)

        m = re.search(r"Received\s+prefixes\s+(\d+)", line)
        if m:
            current["rcv"] = int(m.group(1))

        m = re.search(r"Prefix limit:\s+(\d+)", line)
        if m:
            current["max"] = int(m.group(1))

    if current:
        peers.append(_build_junos_peer(current, hostname))

    return peers


def _build_junos_peer(c: dict, hostname: str) -> PeerLimitData:
    return PeerLimitData(
        host=hostname,
        peer=c["peer"],
        vrf=c.get("vrf", "master"),
        remote_as=c.get("remote_as", 0),
        state=c.get("state", "unknown"),
        received_prefixes=c.get("rcv", 0),
        max_prefix_limit=c.get("max"),
    )


def collect_prefix_limits(task: Task) -> Result:
    platform = task.host.get("platform", "")
    hostname = task.host.name

    if "junos" in platform:
        cmd = "show bgp neighbor"
    else:
        cmd = "show bgp all neighbors"

    result = task.run(task=netmiko_send_command, command_string=cmd)
    output = result[0].result

    if "junos" in platform:
        peers = _parse_junos_neighbors(output, hostname)
    else:
        peers = _parse_cisco_neighbors(output, hostname)

    return Result(host=task.host, result=peers)


def classify(peers: list[PeerLimitData], warn_pct: float, crit_pct: float) -> list[PeerLimitData]:
    for p in peers:
        if p.max_prefix_limit and p.max_prefix_limit > 0:
            p.utilization_pct = round(p.received_prefixes / p.max_prefix_limit * 100, 1)
            if p.utilization_pct >= crit_pct:
                p.severity = "CRITICAL"
            elif p.utilization_pct >= warn_pct:
                p.severity = "WARNING"
            else:
                p.severity = "OK"
        else:
            p.severity = "NO-LIMIT"
    return peers


def render_table(peers: list[PeerLimitData]) -> None:
    order = {"CRITICAL": 0, "WARNING": 1, "NO-LIMIT": 2, "OK": 3, "UNKNOWN": 4}
    sorted_peers = sorted(peers, key=lambda p: (order.get(p.severity, 9), p.host, p.peer))

    header = f"{'HOST':<18} {'PEER':<18} {'AS':>7} {'STATE':<12} {'RCV':>8} {'LIMIT':>8} {'UTIL%':>7}  SEVERITY"
    sep = "-" * len(header)
    print(f"\n{sep}\nBGP PREFIX-LIMIT UTILIZATION REPORT\n{sep}")
    print(header)
    print(sep)

    for p in sorted_peers:
        util = f"{p.utilization_pct:>6.1f}%" if p.utilization_pct is not None else "   n/a "
        limit = str(p.max_prefix_limit) if p.max_prefix_limit else "none"
        tag = {"CRITICAL": "[CRIT]", "WARNING": "[WARN]", "OK": "[ OK ]"}.get(p.severity, f"[{p.severity[:4]:4}]")
        print(f"{p.host:<18} {p.peer:<18} {p.remote_as:>7} {p.state:<12} "
              f"{p.received_prefixes:>8} {limit:>8} {util}  {tag}")

    counts = {k: sum(1 for p in peers if p.severity == k) for k in ("CRITICAL", "WARNING", "OK", "NO-LIMIT")}
    print(sep)
    print(f"Total: {len(peers)} peers  |  CRITICAL: {counts['CRITICAL']}  "
          f"WARNING: {counts['WARNING']}  OK: {counts['OK']}  NO-LIMIT: {counts['NO-LIMIT']}")
    print(sep + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BGP prefix-limit utilization monitor — Nornir + Netmiko"
    )
    parser.add_argument("--hosts", default="inventory/hosts.yaml", metavar="FILE")
    parser.add_argument("--groups", default="inventory/groups.yaml", metavar="FILE")
    parser.add_argument("--defaults", default="inventory/defaults.yaml", metavar="FILE")
    parser.add_argument("--filter", metavar="KEY=VAL[,KEY=VAL]",
                        help="Nornir host filter (e.g. role=edge,site=nyc)")
    parser.add_argument("--warn", type=float, default=75.0, metavar="PCT",
                        help="Warning threshold %% (default: 75)")
    parser.add_argument("--crit", type=float, default=90.0, metavar="PCT",
                        help="Critical threshold %% (default: 90)")
    parser.add_argument("--workers", type=int, default=10, metavar="N")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    for f in (args.hosts, args.groups, args.defaults):
        if not Path(f).exists():
            print(f"ERROR: inventory file not found: {f}", file=sys.stderr)
            return 2

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
        logging={"enabled": False},
    )

    if args.filter:
        f_kwargs = {}
        for token in args.filter.split(","):
            k, _, v = token.partition("=")
            if k.strip() and v.strip():
                f_kwargs[k.strip()] = v.strip()
        nr = nr.filter(F(**f_kwargs))

    if not nr.inventory.hosts:
        print("No hosts matched — check inventory and --filter.", file=sys.stderr)
        return 2

    results = nr.run(task=collect_prefix_limits)

    all_peers: list[PeerLimitData] = []
    failed: list[tuple[str, str]] = []

    for host, multi in results.items():
        if multi.failed:
            failed.append((host, str(multi.exception)))
            logger.error("Failed on %s: %s", host, multi.exception)
        else:
            all_peers.extend(multi[0].result)

    classify(all_peers, args.warn, args.crit)

    if args.json:
        print(json.dumps([asdict(p) for p in all_peers], indent=2))
    else:
        if all_peers:
            render_table(all_peers)
        else:
            print("No BGP prefix-limit data returned.", file=sys.stderr)

    for host, err in failed:
        print(f"ERROR  {host}: {err}", file=sys.stderr)

    has_critical = any(p.severity == "CRITICAL" for p in all_peers)
    return 2 if failed else (1 if has_critical else 0)


if __name__ == "__main__":
    sys.exit(main())
```