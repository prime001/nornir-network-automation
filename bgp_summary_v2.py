Now I have full context on what both scripts do. `bgp_summary.py` is a NAPALM-based neighbor table renderer and `bgp_summary_v2.py` is a DNS/NTP verifier. I'll write a BGP prefix drift detector — baseline snapshot + live comparison — which is a genuinely different workflow.

"""
bgp_prefix_drift.py — BGP Prefix Drift Detector

Collects BGP neighbor state and prefix counts from a fleet of routers,
then compares against a saved JSON baseline to surface drift: new or
missing peers, session state flaps, and significant prefix-count changes.

On the first run (or with --save-baseline), a JSON snapshot is written.
On subsequent runs the live state is diffed and a delta report is printed.
Exit code 0 = clean, 1 = drift or collection errors, 2 = bad arguments.

Usage:
    # Establish or refresh a baseline
    python bgp_prefix_drift.py \
        --hosts inventory/hosts.yaml \
        --groups inventory/groups.yaml \
        --defaults inventory/defaults.yaml \
        --save-baseline bgp_baseline.json

    # Compare live state to baseline
    python bgp_prefix_drift.py --baseline bgp_baseline.json

    # Alert only when prefix count shifts by >=10 % (default 20)
    python bgp_prefix_drift.py --baseline bgp_baseline.json --threshold 10

    # Restrict to edge routers in nyc
    python bgp_prefix_drift.py --baseline bgp_baseline.json --filter role=edge,site=nyc

Prerequisites:
    pip install nornir nornir-napalm nornir-utils napalm

    NAPALM-supported platforms: ios, eos, junos, nxos_ssh.
    Credentials live in defaults.yaml or per-host in hosts.yaml.
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

logger = logging.getLogger(__name__)


def collect_bgp_state(task: Task) -> Result:
    task.run(task=napalm_get, getters=["bgp_neighbors"])
    raw = task.results[1].result.get("bgp_neighbors", {})

    peers = {}
    for vrf, vrf_data in raw.items():
        for peer_ip, peer in vrf_data.get("peers", {}).items():
            rcv = sum(
                af.get("received_prefixes", 0) or 0
                for af in peer.get("address_family", {}).values()
            )
            peers[f"{vrf}/{peer_ip}"] = {
                "vrf": vrf,
                "peer": peer_ip,
                "remote_as": peer.get("remote_as", ""),
                "state": peer.get("connection_state", "unknown"),
                "rcv_prefixes": rcv,
                "uptime": peer.get("uptime", -1) or -1,
            }

    return Result(host=task.host, result=peers)


def snapshot_fleet(nr):
    results = nr.run(task=collect_bgp_state)
    snapshot, errors = {}, []
    for host, multi in results.items():
        if multi.failed:
            errors.append((host, str(multi.exception)))
        else:
            snapshot[host] = multi[0].result
    return snapshot, errors


def diff_snapshots(baseline: dict, live: dict, threshold_pct: float) -> list:
    lines = []
    for host in sorted(set(baseline) | set(live)):
        if host not in baseline:
            lines.append(f"NEW HOST   {host}")
            continue
        if host not in live:
            lines.append(f"LOST HOST  {host}")
            continue

        base_peers, live_peers = baseline[host], live[host]
        for key in sorted(set(base_peers) | set(live_peers)):
            if key not in base_peers:
                p = live_peers[key]
                lines.append(
                    f"NEW PEER   {host:20s}  {key:35s}  AS {p['remote_as']}"
                    f"  state={p['state']}  rcv={p['rcv_prefixes']}"
                )
            elif key not in live_peers:
                p = base_peers[key]
                lines.append(
                    f"LOST PEER  {host:20s}  {key:35s}  AS {p['remote_as']}"
                    f"  was state={p['state']}  was rcv={p['rcv_prefixes']}"
                )
            else:
                b, lv = base_peers[key], live_peers[key]
                if b["state"].lower() != lv["state"].lower():
                    lines.append(
                        f"STATE CHG  {host:20s}  {key:35s}  AS {b['remote_as']}"
                        f"  {b['state']} -> {lv['state']}"
                    )
                base_rcv, live_rcv = b["rcv_prefixes"], lv["rcv_prefixes"]
                if base_rcv > 0:
                    pct = abs(live_rcv - base_rcv) / base_rcv * 100
                elif live_rcv > 0:
                    pct = 100.0
                else:
                    pct = 0.0
                if pct >= threshold_pct:
                    sign = "+" if live_rcv > base_rcv else "-"
                    lines.append(
                        f"PFX DRIFT  {host:20s}  {key:35s}  AS {b['remote_as']}"
                        f"  {base_rcv} -> {live_rcv} ({sign}{pct:.1f}%)"
                    )
    return lines


def build_nornir(args):
    for path in (args.hosts, args.groups, args.defaults):
        if not Path(path).exists():
            logger.error("Inventory file not found: %s", path)
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
        pairs = {}
        for token in args.filter.split(","):
            k, _, v = token.partition("=")
            if k.strip() and v.strip():
                pairs[k.strip()] = v.strip()
        nr = nr.filter(F(**pairs))

    if not nr.inventory.hosts:
        print("No hosts matched — check inventory and --filter.", file=sys.stderr)
        sys.exit(2)
    return nr


def main():
    parser = argparse.ArgumentParser(
        description="BGP prefix drift detector — diff live BGP state against a saved baseline"
    )
    parser.add_argument("--hosts", default="inventory/hosts.yaml", metavar="FILE")
    parser.add_argument("--groups", default="inventory/groups.yaml", metavar="FILE")
    parser.add_argument("--defaults", default="inventory/defaults.yaml", metavar="FILE")
    parser.add_argument(
        "--filter", metavar="KEY=VAL[,KEY=VAL]",
        help="Nornir host filter (e.g. role=edge,site=nyc)",
    )
    parser.add_argument(
        "--baseline", default="bgp_baseline.json", metavar="FILE",
        help="Baseline snapshot file (default: bgp_baseline.json)",
    )
    parser.add_argument(
        "--save-baseline", action="store_true",
        help="Capture a fresh baseline and exit",
    )
    parser.add_argument(
        "--threshold", type=float, default=20.0, metavar="PCT",
        help="Prefix-count delta %% that triggers a drift alert (default: 20)",
    )
    parser.add_argument("--workers", type=int, default=10, metavar="N")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    nr = build_nornir(args)
    live_snapshot, errors = snapshot_fleet(nr)

    for host, err in errors:
        print(f"ERROR  {host}: {err}", file=sys.stderr)

    if not live_snapshot:
        print("No data collected from any host.", file=sys.stderr)
        sys.exit(1)

    if args.save_baseline:
        payload = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": live_snapshot,
        }
        with open(args.baseline, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Baseline saved: {args.baseline}  ({len(live_snapshot)} hosts)")
        sys.exit(0 if not errors else 1)

    if not Path(args.baseline).exists():
        print(
            f"No baseline at {args.baseline}. Run with --save-baseline to create one.",
            file=sys.stderr,
        )
        sys.exit(2)

    with open(args.baseline) as fh:
        baseline_snapshot = json.load(fh)["snapshot"]

    drift = diff_snapshots(baseline_snapshot, live_snapshot, args.threshold)

    if drift:
        print(f"\nBGP DRIFT DETECTED  ({len(drift)} change(s))\n")
        for line in drift:
            print(" ", line)
        print()
    else:
        print("OK — no BGP drift detected.")

    sys.exit(1 if (drift or errors) else 0)


if __name__ == "__main__":
    main()