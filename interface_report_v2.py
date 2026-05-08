All three existing scripts are: basic status (003), neighbor discovery (013), error-threshold monitoring (023). I'll write 033 as an **interface flap detection and stability scorer** using NAPALM's `last_flapped` data — a distinct operational use case.

```python
"""
033_interface_report.py — Interface Flap Detection and Stability Analysis

Purpose:
    Polls network devices via NAPALM and reports interface stability based on
    last-flap time. Categorizes each interface into stability tiers (stable,
    degraded, critical) and optionally runs two timed polls to catch live
    flaps in a narrow observation window. Useful for identifying unstable links
    that are currently up but causing intermittent user impact.

Usage:
    # Single device, print stability table
    python 033_interface_report.py --host 192.168.1.1 --username admin

    # Show only interfaces that flapped within the last 4 hours
    python 033_interface_report.py --host 192.168.1.1 --username admin \
        --window-hours 4 --unstable-only

    # Two-poll live-flap detection with 60-second interval
    python 033_interface_report.py --host 192.168.1.1 --username admin \
        --live-poll --poll-interval 60

    # Full inventory, export JSON
    python 033_interface_report.py --inventory hosts.yaml \
        --group core --output json --out-file flaps.json

Prerequisites:
    pip install nornir nornir-napalm napalm tabulate
    NAPALM-compatible device (IOS, EOS, NXOS, JunOS, IOS-XR)
    Devices must support 'last_flapped' via NAPALM get_interfaces()
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get
from tabulate import tabulate

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("interface_flap_detector")

TIER_STABLE = "STABLE"
TIER_DEGRADED = "DEGRADED"
TIER_CRITICAL = "CRITICAL"
TIER_UNKNOWN = "UNKNOWN"


@dataclass
class InterfaceFlapRow:
    host: str
    interface: str
    is_up: bool
    is_enabled: bool
    speed_mbps: Optional[float]
    last_flapped_sec: Optional[float]
    last_flapped_hrs: Optional[float]
    tier: str
    live_flap_detected: bool


def _tier(last_sec: Optional[float], window_hours: float) -> str:
    if last_sec is None or last_sec < 0:
        return TIER_UNKNOWN
    hours = last_sec / 3600
    if hours < 1:
        return TIER_CRITICAL
    if hours < window_hours:
        return TIER_DEGRADED
    return TIER_STABLE


def collect_interfaces(task: Task, window_hours: float,
                       baseline: Optional[Dict[str, float]]) -> Result:
    r = task.run(task=napalm_get, getters=["interfaces"])
    ifaces = r[0].result.get("interfaces", {})

    rows: List[InterfaceFlapRow] = []
    for name, data in ifaces.items():
        raw_flapped = data.get("last_flapped")
        last_sec: Optional[float] = None
        if raw_flapped is not None:
            try:
                last_sec = float(raw_flapped)
                if last_sec < 0:
                    last_sec = None
            except (TypeError, ValueError):
                pass

        speed = None
        try:
            bps = float(data.get("speed", -1))
            if bps > 0:
                speed = round(bps / 1_000_000, 1)
        except (TypeError, ValueError):
            pass

        live_flap = False
        if baseline and last_sec is not None:
            prev = baseline.get(f"{task.host.name}:{name}")
            if prev is not None and last_sec < prev:
                live_flap = True

        rows.append(InterfaceFlapRow(
            host=task.host.name,
            interface=name,
            is_up=bool(data.get("is_up", False)),
            is_enabled=bool(data.get("is_enabled", True)),
            speed_mbps=speed,
            last_flapped_sec=last_sec,
            last_flapped_hrs=round(last_sec / 3600, 2) if last_sec is not None else None,
            tier=_tier(last_sec, window_hours),
            live_flap_detected=live_flap,
        ))

    rows.sort(key=lambda r: (
        [TIER_CRITICAL, TIER_DEGRADED, TIER_UNKNOWN, TIER_STABLE].index(r.tier),
        r.last_flapped_sec if r.last_flapped_sec is not None else float("inf"),
    ))
    return Result(host=task.host, result=rows)


def build_baseline(nr) -> Dict[str, float]:
    baseline: Dict[str, float] = {}
    results = nr.run(task=napalm_get, getters=["interfaces"])
    for host_name, multi in results.items():
        if multi.failed:
            continue
        ifaces = multi[0].result.get("interfaces", {})
        for name, data in ifaces.items():
            try:
                val = float(data.get("last_flapped", -1))
                if val >= 0:
                    baseline[f"{host_name}:{name}"] = val
            except (TypeError, ValueError):
                pass
    return baseline


def print_table(rows: List[InterfaceFlapRow], unstable_only: bool) -> None:
    tier_order = {TIER_CRITICAL: 0, TIER_DEGRADED: 1, TIER_UNKNOWN: 2, TIER_STABLE: 3}
    display = [r for r in rows if r.tier in (TIER_CRITICAL, TIER_DEGRADED)] if unstable_only else rows
    if not display:
        print("No interfaces to display.")
        return
    headers = ["Host", "Interface", "Up", "En", "Speed(M)",
               "LastFlap(h)", "Tier", "LiveFlap"]
    table = [
        [
            r.host, r.interface,
            "Y" if r.is_up else "N",
            "Y" if r.is_enabled else "N",
            r.speed_mbps if r.speed_mbps is not None else "-",
            f"{r.last_flapped_hrs:.2f}" if r.last_flapped_hrs is not None else "?",
            r.tier,
            "!!" if r.live_flap_detected else "",
        ]
        for r in display
    ]
    print(tabulate(table, headers=headers, tablefmt="simple"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interface flap detection and stability analysis via Nornir/NAPALM"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", help="Single device hostname or IP")
    target.add_argument("--inventory", help="Path to Nornir hosts.yaml")

    parser.add_argument("--username", default=os.environ.get("NET_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("NET_PASS"))
    parser.add_argument("--platform", default="ios",
                        help="NAPALM driver: ios, eos, nxos, junos (default: ios)")
    parser.add_argument("--group", help="Filter inventory by Nornir group")
    parser.add_argument("--window-hours", type=float, default=24.0,
                        help="Hours defining the DEGRADED window (default: 24)")
    parser.add_argument("--unstable-only", action="store_true",
                        help="Show only CRITICAL/DEGRADED interfaces")
    parser.add_argument("--live-poll", action="store_true",
                        help="Run two polls to detect flaps in the observation window")
    parser.add_argument("--poll-interval", type=int, default=60,
                        help="Seconds between live polls (default: 60)")
    parser.add_argument("--output", choices=["table", "json"], default="table")
    parser.add_argument("--out-file", help="Write output to file")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    password = args.password or ""
    if not password:
        import getpass
        password = getpass.getpass(f"Password for {args.username}: ")

    if args.host:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": 1}},
            inventory={"plugin": "SimpleInventory",
                       "options": {"host_file": None}},
            logging={"enabled": False},
        )
        nr.inventory.hosts[args.host] = Host(
            name=args.host, hostname=args.host,
            username=args.username, password=password,
            platform=args.platform, groups=Groups(), defaults=Defaults(),
        )
    else:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": 10}},
            inventory={"plugin": "SimpleInventory",
                       "options": {"host_file": args.inventory}},
            logging={"enabled": False},
        )
        if args.group:
            nr = nr.filter(groups__contains=args.group)

    baseline: Optional[Dict[str, float]] = None
    if args.live_poll:
        print(f"Baseline poll… waiting {args.poll_interval}s for observation window.")
        baseline = build_baseline(nr)
        time.sleep(args.poll_interval)

    results = nr.run(
        task=collect_interfaces,
        window_hours=args.window_hours,
        baseline=baseline,
    )

    all_rows: List[InterfaceFlapRow] = []
    for host_name, multi in results.items():
        if multi.failed:
            print(f"[ERROR] {host_name}: {multi.exception}", file=sys.stderr)
            continue
        all_rows.extend(multi[1].result if len(multi) > 1 else [])

    if not all_rows:
        print("No data collected.")
        sys.exit(1)

    if args.output == "table":
        text = None
        print_table(all_rows, args.unstable_only)
    else:
        text = json.dumps([asdict(r) for r in all_rows], indent=2)
        if args.out_file:
            with open(args.out_file, "w") as f:
                f.write(text)
            print(f"JSON written to {args.out_file}")
        else:
            print(text)

    critical = sum(1 for r in all_rows if r.tier == TIER_CRITICAL)
    degraded = sum(1 for r in all_rows if r.tier == TIER_DEGRADED)
    live = sum(1 for r in all_rows if r.live_flap_detected)
    print(
        f"\nSummary: {len(all_rows)} interfaces — "
        f"{critical} CRITICAL (<1h), {degraded} DEGRADED (<{args.window_hours}h)"
        + (f", {live} live flaps detected" if args.live_poll else "")
    )
    sys.exit(1 if critical or live else 0)


if __name__ == "__main__":
    main()
```