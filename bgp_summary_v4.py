The write was denied. Here is the complete script content:

```python
"""BGP Peer Health Monitor using Nornir.

Purpose:
    Checks BGP neighbor states across a fleet of routers, flags non-Established
    sessions, and warns when received prefix counts breach configured thresholds.
    Designed for operational monitoring rather than one-shot reporting.

Usage:
    python bgp_peer_health.py --hosts inventory/hosts.yaml --groups inventory/groups.yaml \
        --defaults inventory/defaults.yaml [--warn-prefix-threshold 50000] \
        [--filter-host router1] [--filter-group edge] [--output json]

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko

    Inventory files must define 'platform' per host (cisco_ios, eos, junos, etc.)
    Credentials via inventory or env vars NORNIR_USER / NORNIR_PASS.
"""

import argparse
import json
import logging
import os
import sys
from typing import Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BGP_COMMANDS = {
    "cisco_ios": "show ip bgp summary",
    "cisco_xe": "show ip bgp summary",
    "cisco_xr": "show bgp ipv4 unicast summary",
    "eos": "show ip bgp summary",
    "junos": "show bgp summary",
}


def parse_ios_bgp_summary(output: str, warn_threshold: int) -> list[dict]:
    peers = []
    in_neighbor_table = False
    for line in output.splitlines():
        if line.startswith("Neighbor"):
            in_neighbor_table = True
            continue
        if not in_neighbor_table:
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        neighbor = parts[0]
        state_or_prefixes = parts[-1]
        up_down = parts[8]
        is_numeric = state_or_prefixes.lstrip("-").isdigit()
        state = "Established" if is_numeric else state_or_prefixes
        prefix_count = int(state_or_prefixes) if is_numeric else 0
        peers.append({
            "neighbor": neighbor,
            "as": parts[2],
            "up_down": up_down,
            "state": state,
            "prefixes_received": prefix_count,
            "warn": state != "Established" or (warn_threshold and prefix_count > warn_threshold),
        })
    return peers


def parse_junos_bgp_summary(output: str, warn_threshold: int) -> list[dict]:
    peers = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 6 or not parts[0].replace(".", "").replace(":", "").isalnum():
            continue
        try:
            neighbor = parts[0]
            state = parts[3]
            prefixes = int(parts[4]) if parts[4].isdigit() else 0
            up_down = parts[2]
            peers.append({
                "neighbor": neighbor,
                "as": parts[1],
                "up_down": up_down,
                "state": state,
                "prefixes_received": prefixes,
                "warn": state != "Establ" or (warn_threshold and prefixes > warn_threshold),
            })
        except (IndexError, ValueError):
            continue
    return peers


def check_bgp_health(task: Task, warn_threshold: int) -> Result:
    platform = task.host.platform or "cisco_ios"
    command = BGP_COMMANDS.get(platform, BGP_COMMANDS["cisco_ios"])

    try:
        result = task.run(
            task=netmiko_send_command,
            command_string=command,
            use_textfsm=False,
        )
    except Exception as exc:
        return Result(host=task.host, failed=True, exception=exc,
                      result={"error": str(exc), "peers": []})

    raw = result[0].result
    if "junos" in platform:
        peers = parse_junos_bgp_summary(raw, warn_threshold)
    else:
        peers = parse_ios_bgp_summary(raw, warn_threshold)

    not_established = [p for p in peers if p["state"] not in ("Established", "Establ")]
    return Result(
        host=task.host,
        result={
            "total_peers": len(peers),
            "not_established": len(not_established),
            "peers": peers,
            "raw": raw,
        },
    )


def build_report(agg_result: Any) -> dict:
    report: dict[str, Any] = {
        "devices": {},
        "summary": {"total_peers": 0, "degraded_peers": 0, "failed_hosts": []},
    }
    for host, multi_result in agg_result.items():
        if multi_result.failed:
            report["summary"]["failed_hosts"].append(host)
            report["devices"][host] = {"error": str(multi_result.exception)}
            continue
        data = multi_result[0].result
        report["devices"][host] = data
        report["summary"]["total_peers"] += data.get("total_peers", 0)
        report["summary"]["degraded_peers"] += data.get("not_established", 0)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="BGP peer health monitor via Nornir")
    parser.add_argument("--hosts", default="inventory/hosts.yaml")
    parser.add_argument("--groups", default="inventory/groups.yaml")
    parser.add_argument("--defaults", default="inventory/defaults.yaml")
    parser.add_argument("--filter-host", help="Run against a single host name")
    parser.add_argument("--filter-group", help="Run against a named group")
    parser.add_argument("--warn-prefix-threshold", type=int, default=0,
                        help="Warn if any peer exceeds this received prefix count (0=disabled)")
    parser.add_argument("--output", choices=["table", "json"], default="table")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

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
    )

    if os.environ.get("NORNIR_USER"):
        nr.inventory.defaults.username = os.environ["NORNIR_USER"]
    if os.environ.get("NORNIR_PASS"):
        nr.inventory.defaults.password = os.environ["NORNIR_PASS"]

    target = nr
    if args.filter_host:
        target = nr.filter(F(name=args.filter_host))
    elif args.filter_group:
        target = nr.filter(F(groups__contains=args.filter_group))

    if not target.inventory.hosts:
        logger.error("No hosts matched the filter criteria.")
        return 1

    result = target.run(
        task=check_bgp_health,
        warn_threshold=args.warn_prefix_threshold,
    )

    report = build_report(result)

    if args.output == "json":
        for d in report["devices"].values():
            d.pop("raw", None)
        print(json.dumps(report, indent=2))
    else:
        for host, data in report["devices"].items():
            if "error" in data:
                print(f"\n{'='*60}\n{host}: CONNECTION FAILED — {data['error']}")
                continue
            peers = data.get("peers", [])
            print(f"\n{'='*60}\n{host}  ({len(peers)} peers, {data['not_established']} not established)")
            print(f"  {'Neighbor':<20} {'AS':<8} {'State':<14} {'Prefixes':>9}  {'Up/Down':<14}  {'!'}")
            for p in peers:
                flag = "WARN" if p["warn"] else ""
                print(f"  {p['neighbor']:<20} {p['as']:<8} {p['state']:<14} "
                      f"{p['prefixes_received']:>9}  {p['up_down']:<14}  {flag}")

        s = report["summary"]
        print(f"\n{'='*60}")
        print(f"Summary: {s['total_peers']} total peers, "
              f"{s['degraded_peers']} not established, "
              f"{len(s['failed_hosts'])} unreachable hosts")
        if s["failed_hosts"]:
            print(f"  Unreachable: {', '.join(s['failed_hosts'])}")

    return 1 if report["summary"]["degraded_peers"] or report["summary"]["failed_hosts"] else 0


if __name__ == "__main__":
    sys.exit(main())
```

This script differentiates from the existing BGP summary scripts by framing the problem as **health monitoring with exit codes and threshold alerting** rather than plain reporting — it returns exit code 1 if any peer is down or any host unreachable, making it CI/cron-friendly. Key additions over a basic summary: per-platform command dispatch (`cisco_ios/xe/xr`, `eos`, `junos`), a `--warn-prefix-threshold` flag to catch prefix-count anomalies, `--output json` for pipeline integration, and structured `summary` block aggregating degraded counts across all devices.