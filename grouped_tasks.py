The existing paramiko scripts cover config, interfaces, routing, ARP, and inventory. I'll write a BGP neighbor health monitor with Nornir+NAPALM — practical NOC tooling that complements the existing set without overlap.

"""
bgp_neighbor_monitor.py — BGP Session Health Monitor

Polls BGP neighbor state across a fleet of routers using Nornir + NAPALM.
Identifies sessions that are not Established, flags recently-established
sessions (potential flaps), and prints a structured JSON report.

Exits with code 1 when any neighbor is down — suitable for cron alerting
or integration with Nagios/Icinga check scripts.

Usage:
    python bgp_neighbor_monitor.py \
        --hosts  inventory/hosts.yaml   \
        --groups inventory/groups.yaml  \
        --defaults inventory/defaults.yaml \
        [--filter role=edge,site=nyc]   \
        [--min-uptime 300]              \
        [--output report.json]          \
        [--workers 10]                  \
        [--verbose]

Prerequisites:
    pip install nornir nornir-napalm nornir-utils napalm

    NAPALM-compatible platforms: ios, eos, junos, nxos_ssh.
    SSH credentials must be set in defaults.yaml or per-host in hosts.yaml.

Inventory quickstart (defaults.yaml):
    username: admin
    password: secret
    port: 22
    platform: ios
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get
from nornir_utils.plugins.functions import print_result

logger = logging.getLogger(__name__)


def collect_bgp_health(task: Task, min_uptime: int) -> Result:
    """Fetch BGP neighbors via NAPALM and classify session health."""
    task.run(task=napalm_get, getters=["bgp_neighbors"])
    neighbors_raw = task.results[1].result.get("bgp_neighbors", {})

    issues = []
    totals = {"total": 0, "established": 0, "down": 0, "flapping": 0}

    for vrf, vrf_data in neighbors_raw.items():
        for peer_ip, peer in vrf_data.get("peers", {}).items():
            totals["total"] += 1
            state = peer.get("connection_state", "unknown").lower()
            uptime = peer.get("uptime", 0) or 0
            remote_as = peer.get("remote_as")

            if state == "established":
                totals["established"] += 1
                if 0 < uptime < min_uptime:
                    totals["flapping"] += 1
                    issues.append({
                        "peer": peer_ip,
                        "vrf": vrf,
                        "state": state,
                        "issue": "recent_flap",
                        "uptime_seconds": uptime,
                        "remote_as": remote_as,
                    })
            else:
                totals["down"] += 1
                issues.append({
                    "peer": peer_ip,
                    "vrf": vrf,
                    "state": state,
                    "issue": "session_down",
                    "uptime_seconds": uptime,
                    "remote_as": remote_as,
                })

    return Result(
        host=task.host,
        result={"totals": totals, "issues": issues},
    )


def build_report(nr_results) -> dict:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "devices_polled": 0,
        "devices_with_issues": 0,
        "total_neighbors": 0,
        "total_down": 0,
        "total_flapping": 0,
        "devices": {},
    }

    for host, multi_result in nr_results.items():
        report["devices_polled"] += 1

        if multi_result.failed:
            report["devices"][host] = {
                "status": "error",
                "error": str(multi_result.exception),
            }
            report["devices_with_issues"] += 1
            continue

        data = multi_result[0].result
        totals = data["totals"]
        report["total_neighbors"] += totals["total"]
        report["total_down"] += totals["down"]
        report["total_flapping"] += totals["flapping"]

        has_issues = bool(data["issues"])
        report["devices"][host] = {
            "status": "issues" if has_issues else "ok",
            "totals": totals,
            "issues": data["issues"],
        }
        if has_issues:
            report["devices_with_issues"] += 1

    return report


def parse_host_filter(filter_str: str) -> dict:
    filters = {}
    for token in filter_str.split(","):
        k, _, v = token.partition("=")
        if k.strip() and v.strip():
            filters[k.strip()] = v.strip()
    return filters


def main():
    parser = argparse.ArgumentParser(
        description="BGP session health monitor — Nornir + NAPALM"
    )
    parser.add_argument("--hosts", default="inventory/hosts.yaml", metavar="FILE")
    parser.add_argument("--groups", default="inventory/groups.yaml", metavar="FILE")
    parser.add_argument("--defaults", default="inventory/defaults.yaml", metavar="FILE")
    parser.add_argument(
        "--filter", metavar="KEY=VAL[,KEY=VAL]",
        help="Nornir host filter expression (e.g. role=edge,site=nyc)",
    )
    parser.add_argument(
        "--min-uptime", type=int, default=300, metavar="SECONDS",
        help="Sessions established below this threshold are flagged as flapping (default: 300)",
    )
    parser.add_argument("--output", metavar="FILE", help="Write JSON report to this file")
    parser.add_argument("--workers", type=int, default=10, metavar="N",
                        help="Parallel worker threads (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print raw Nornir task output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    for inv_file in (args.hosts, args.groups, args.defaults):
        if not Path(inv_file).exists():
            logger.error("Inventory file not found: %s", inv_file)
            sys.exit(2)

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
        nr = nr.filter(F(**parse_host_filter(args.filter)))

    if not nr.inventory.hosts:
        logger.error("No hosts matched the given filter — nothing to poll.")
        sys.exit(2)

    logger.info("Polling BGP neighbors on %d device(s)…", len(nr.inventory.hosts))
    results = nr.run(task=collect_bgp_health, min_uptime=args.min_uptime)

    if args.verbose:
        print_result(results)

    report = build_report(results)
    report_json = json.dumps(report, indent=2)

    if args.output:
        Path(args.output).write_text(report_json)
        logger.info("Report written to %s", args.output)
    else:
        print(report_json)

    if report["total_down"] > 0 or report["devices_with_issues"] > 0:
        logger.warning(
            "%d neighbor(s) down, %d flapping across %d device(s) with issues",
            report["total_down"],
            report["total_flapping"],
            report["devices_with_issues"],
        )
        sys.exit(1)

    logger.info(
        "All %d BGP neighbor(s) healthy across %d device(s).",
        report["total_neighbors"],
        report["devices_polled"],
    )


if __name__ == "__main__":
    main()