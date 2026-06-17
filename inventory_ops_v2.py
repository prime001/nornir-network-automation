Writing a route-snapshot-and-drift-detection script — the next unoccupied slot (027) that doesn't overlap any existing script.

```python
"""
Route Table Snapshot and Drift Detection
=========================================
Purpose:
    Capture routing table snapshots across network devices and detect route
    changes between runs. Useful for change-window validation, post-change
    verification, and detecting unauthorized route advertisements.

Usage:
    # Save a baseline snapshot before a maintenance window
    python 027_route_snapshot.py --save --output-dir ./snapshots

    # Compare current routes against the saved baseline
    python 027_route_snapshot.py --compare --output-dir ./snapshots

    # Check a specific prefix across all devices
    python 027_route_snapshot.py --prefix 10.0.0.0/8 --save

    # Target a specific host group
    python 027_route_snapshot.py --group core --compare

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: config.yaml, hosts.yaml, groups.yaml, defaults.yaml
    Devices must respond to 'show ip route' or platform equivalent.

Exit codes:
    0 — success, no changes detected
    1 — collection or baseline error
    2 — route drift detected (compare mode)
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ROUTE_COMMANDS = {
    "cisco_ios": "show ip route",
    "cisco_nxos": "show ip route",
    "cisco_xr": "show route ipv4",
    "juniper_junos": "show route",
    "arista_eos": "show ip route",
}

# Matches IOS/NX-OS/EOS route lines: protocol prefix ... via nexthop
_ROUTE_RE = re.compile(
    r"^[OSBDERICL*\s>]\s*"
    r"(\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?)"
    r".*?(?:via\s+(\d{1,3}(?:\.\d{1,3}){3}))?",
    re.MULTILINE,
)


def collect_routes(task: Task, prefix_filter: str = None) -> Result:
    platform = task.host.platform or "cisco_ios"
    command = ROUTE_COMMANDS.get(platform, "show ip route")
    if prefix_filter:
        command = f"{command} {prefix_filter}"
    result = task.run(task=netmiko_send_command, command_string=command)
    return Result(host=task.host, result=result.result)


def parse_routes(raw: str) -> dict:
    routes = {}
    for m in _ROUTE_RE.finditer(raw):
        prefix = m.group(1)
        if "/" not in prefix:
            prefix += "/32"
        nexthop = m.group(2) or "directly connected"
        routes.setdefault(prefix, [])
        if nexthop not in routes[prefix]:
            routes[prefix].append(nexthop)
    return routes


def save_snapshot(snapshot_dir: Path, hostname: str, routes: dict) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = snapshot_dir / f"{hostname}_{ts}.json"
    path.write_text(
        json.dumps({"timestamp": ts, "host": hostname, "routes": routes}, indent=2)
    )
    logger.info("Saved %s — %d prefixes", path.name, len(routes))


def load_latest_snapshot(snapshot_dir: Path, hostname: str) -> dict | None:
    candidates = sorted(snapshot_dir.glob(f"{hostname}_*.json"))
    if not candidates:
        return None
    data = json.loads(candidates[-1].read_text())
    logger.info("Baseline: %s (%d prefixes)", candidates[-1].name, len(data["routes"]))
    return data


def diff_routes(baseline: dict, current: dict) -> dict:
    return {
        "added": {p: current[p] for p in current if p not in baseline},
        "removed": {p: baseline[p] for p in baseline if p not in current},
        "changed": {
            p: {"before": baseline[p], "after": current[p]}
            for p in current
            if p in baseline and sorted(current[p]) != sorted(baseline[p])
        },
    }


def print_diff(hostname: str, diff: dict) -> None:
    total = sum(len(v) for v in diff.values())
    if not total:
        print(f"  {hostname}: no route changes")
        return
    print(f"  {hostname}: {total} change(s)")
    for prefix, nexthops in diff["added"].items():
        print(f"    [+] {prefix}  via {', '.join(nexthops)}")
    for prefix, nexthops in diff["removed"].items():
        print(f"    [-] {prefix}  via {', '.join(nexthops)}")
    for prefix, chg in diff["changed"].items():
        print(f"    [~] {prefix}  {', '.join(chg['before'])} -> {', '.join(chg['after'])}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Snapshot routing tables and detect drift between runs."
    )
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--host", help="Limit to a single hostname")
    p.add_argument("--group", help="Limit to a nornir host group")
    p.add_argument("--prefix", help="Restrict to a specific prefix (e.g. 10.0.0.0/8)")
    p.add_argument("--output-dir", default="./route_snapshots", help="Snapshot storage directory")
    p.add_argument("--username", help="Override inventory username")
    p.add_argument("--password", help="Override inventory password")
    p.add_argument("-v", "--verbose", action="store_true")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--save", action="store_true", help="Save a new snapshot")
    mode.add_argument("--compare", action="store_true", help="Compare against latest snapshot")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(config_file=args.config)
    if args.host:
        nr = nr.filter(name=args.host)
    if args.group:
        nr = nr.filter(groups=args.group)
    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if not nr.inventory.hosts:
        logger.error("No hosts matched filters.")
        sys.exit(1)

    logger.info("Collecting routes from %d host(s)...", len(nr.inventory.hosts))
    results = nr.run(task=collect_routes, prefix_filter=args.prefix)

    if args.verbose:
        print_result(results)

    snapshot_dir = Path(args.output_dir)
    exit_code = 0

    print(f"\n{'=' * 58}")
    mode_label = "Snapshot" if args.save else "Comparison"
    print(f"Route {mode_label}  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 58}")

    for hostname, multi in results.items():
        if multi.failed:
            logger.error("%s: collection failed — %s", hostname, multi[0].exception)
            exit_code = 1
            continue

        current = parse_routes(multi[0].result)

        if args.save:
            save_snapshot(snapshot_dir, hostname, current)
            print(f"  {hostname}: saved {len(current)} prefixes")
        else:
            baseline = load_latest_snapshot(snapshot_dir, hostname)
            if baseline is None:
                print(f"  {hostname}: no baseline found — run --save first")
                exit_code = 1
                continue
            diff = diff_routes(baseline["routes"], current)
            print_diff(hostname, diff)
            if any(diff.values()):
                exit_code = 2

    print(f"{'=' * 58}\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```