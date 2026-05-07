The files aren't local — this is a generation task. I'll write a BGP summary script with a distinct angle from the existing three (002/012/022): multi-VRF support, prefix-count threshold alerting, and JSON/CSV export for ops reporting.

```python
"""
BGP Summary with Multi-VRF Support and Threshold Alerting

Collects BGP neighbor state and prefix counts across one or more VRFs on
network devices managed by Nornir, then flags neighbors whose received-prefix
counts fall outside configured thresholds.  Results are printed to stdout and
optionally exported to JSON or CSV for integration with dashboards or ticketing.

Prerequisites:
    pip install nornir nornir-netmiko netmiko

Usage:
    python 032_bgp_summary.py --hosts router1,router2 --platform cisco_ios
    python 032_bgp_summary.py --hosts router1 --vrf MGMT --min-prefixes 1
    python 032_bgp_summary.py --hosts router1 --export-json bgp_state.json
    python 032_bgp_summary.py --hosts router1 --export-csv bgp_state.csv

Supported platforms: cisco_ios, cisco_nxos, arista_eos
"""

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from nornir import InitNornir
from nornir.core.inventory import Defaults, Group, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

LOG = logging.getLogger(__name__)

BGP_COMMANDS = {
    "cisco_ios": "show ip bgp {vrf}summary",
    "cisco_nxos": "show bgp {vrf}summary",
    "arista_eos": "show ip bgp {vrf}summary",
}

NEIGHBOR_RE = re.compile(
    r"^(?P<neighbor>\d+\.\d+\.\d+\.\d+)\s+"
    r"\S+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+"
    r"(?P<prefixes>\d+|Never|Idle|Active|Connect|OpenSent|OpenConfirm|Established)",
    re.MULTILINE,
)

STATE_RE = re.compile(
    r"^(?P<neighbor>\d+\.\d+\.\d+\.\d+)\s+\S+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+"
    r"(?P<state>Idle|Active|Connect|OpenSent|OpenConfirm)",
    re.MULTILINE,
)


def parse_bgp_output(raw: str) -> list[dict[str, Any]]:
    neighbors = []
    for match in NEIGHBOR_RE.finditer(raw):
        neighbor = match.group("neighbor")
        pfx_raw = match.group("prefixes")
        try:
            prefix_count = int(pfx_raw)
            state = "Established"
        except ValueError:
            prefix_count = 0
            state = pfx_raw
        neighbors.append({"neighbor": neighbor, "state": state, "prefixes": prefix_count})
    return neighbors


def collect_bgp_summary(task: Task, vrfs: list[str], platform: str) -> Result:
    cmd_template = BGP_COMMANDS.get(platform, BGP_COMMANDS["cisco_ios"])
    all_neighbors = []
    for vrf in vrfs:
        vrf_part = f"vrf {vrf} " if vrf and vrf.lower() != "default" else ""
        cmd = cmd_template.format(vrf=vrf_part)
        try:
            result = task.run(netmiko_send_command, command_string=cmd)
            parsed = parse_bgp_output(result.result)
            for entry in parsed:
                entry["vrf"] = vrf
            all_neighbors.extend(parsed)
        except Exception as exc:
            LOG.warning("%s: failed to collect BGP for VRF %s: %s", task.host.name, vrf, exc)
    return Result(host=task.host, result=all_neighbors)


def build_inventory(hosts: list[str], username: str, password: str, platform: str) -> Inventory:
    host_objects = {
        h: Host(name=h, hostname=h, username=username, password=password,
                platform=platform, groups=["bgp_targets"])
        for h in hosts
    }
    return Inventory(
        hosts=Hosts(host_objects),
        groups=Groups({"bgp_targets": Group(name="bgp_targets")}),
        defaults=Defaults(),
    )


def evaluate_thresholds(
    records: list[dict], min_prefixes: int, max_prefixes: int
) -> list[dict]:
    alerts = []
    for r in records:
        if r["state"] != "Established":
            alerts.append({**r, "alert": f"neighbor not established: {r['state']}"})
        elif r["prefixes"] < min_prefixes:
            alerts.append({**r, "alert": f"prefix count {r['prefixes']} < min {min_prefixes}"})
        elif max_prefixes and r["prefixes"] > max_prefixes:
            alerts.append({**r, "alert": f"prefix count {r['prefixes']} > max {max_prefixes}"})
    return alerts


def main() -> int:
    parser = argparse.ArgumentParser(description="BGP summary with multi-VRF and threshold alerting")
    parser.add_argument("--hosts", required=True, help="Comma-separated list of device hostnames/IPs")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--platform", default="cisco_ios", choices=BGP_COMMANDS.keys())
    parser.add_argument("--vrf", default="default", help="Comma-separated VRF list (use 'default' for global)")
    parser.add_argument("--min-prefixes", type=int, default=0, help="Alert if prefix count below this")
    parser.add_argument("--max-prefixes", type=int, default=0, help="Alert if prefix count above this (0=disabled)")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--export-json", metavar="FILE", help="Export results to JSON")
    parser.add_argument("--export-csv", metavar="FILE", help="Export results to CSV")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]
    vrf_list = [v.strip() for v in args.vrf.split(",") if v.strip()]

    inventory = build_inventory(host_list, args.username, args.password, args.platform)
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={"plugin": "SimpleInventory"},
        logging={"enabled": False},
    )
    nr.inventory = inventory

    results = nr.run(task=collect_bgp_summary, vrfs=vrf_list, platform=args.platform)

    all_records: list[dict] = []
    timestamp = datetime.now(timezone.utc).isoformat()
    for host_name, multi_result in results.items():
        for result in multi_result:
            if result.failed:
                LOG.error("%s: task failed: %s", host_name, result.exception)
                continue
            for entry in (result.result or []):
                all_records.append({"host": host_name, "timestamp": timestamp, **entry})

    alerts = evaluate_thresholds(all_records, args.min_prefixes, args.max_prefixes)

    print(f"\n{'HOST':<20} {'VRF':<12} {'NEIGHBOR':<16} {'STATE':<14} {'PREFIXES':>8}")
    print("-" * 74)
    for r in all_records:
        flag = " [ALERT]" if any(a["neighbor"] == r["neighbor"] and a["host"] == r["host"] for a in alerts) else ""
        print(f"{r['host']:<20} {r['vrf']:<12} {r['neighbor']:<16} {r['state']:<14} {r['prefixes']:>8}{flag}")

    if alerts:
        print(f"\nALERTS ({len(alerts)}):")
        for a in alerts:
            print(f"  {a['host']} / {a['vrf']} / {a['neighbor']}: {a['alert']}")

    if args.export_json:
        with open(args.export_json, "w") as fh:
            json.dump({"timestamp": timestamp, "records": all_records, "alerts": alerts}, fh, indent=2)
        print(f"\nJSON exported to {args.export_json}")

    if args.export_csv:
        fieldnames = ["host", "timestamp", "vrf", "neighbor", "state", "prefixes"]
        with open(args.export_csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_records)
        print(f"CSV exported to {args.export_csv}")

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
```