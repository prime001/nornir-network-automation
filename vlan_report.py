The user instructions say to output only the script content. Here it is:

```
"""
007_vlan_report.py — VLAN Database Report
```

Actually, re-reading your instructions — you want the raw script content only. Here it is:

---

"""
007_vlan_report.py — VLAN Database Report

Collects the VLAN database from one or more switches via NAPALM and
produces a per-device table showing VLAN ID, name, and member interfaces.
Ghost VLANs (defined but carrying no active interfaces) are flagged with [!].

Usage:
    python 007_vlan_report.py
    python 007_vlan_report.py --filter role=access,site=nyc
    python 007_vlan_report.py --vlan-id 10,20,100-199
    python 007_vlan_report.py --ghost-only
    python 007_vlan_report.py --output vlans.json --format json

Prerequisites:
    pip install nornir nornir-napalm nornir-utils

Inventory quickstart (inventory/hosts.yaml):
    sw1:
      hostname: 192.168.1.10
      platform: ios
      groups:
        - access_switches
"""

import argparse
import csv
import json
import logging
import sys
from io import StringIO
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_COL_W = (6, 32, 6, 50)
_HEADERS = ("ID", "Name", "Ghost", "Interfaces")
_SEP = "-" * (sum(_COL_W) + 2 * (len(_COL_W) - 1))


def parse_host_filter(filter_str: str) -> dict:
    result = {}
    for token in filter_str.split(","):
        k, _, v = token.partition("=")
        if k.strip() and v.strip():
            result[k.strip()] = v.strip()
    return result


def parse_vlan_filter(spec: str) -> set[int]:
    """Parse '10,20,100-199' into a set of integer VLAN IDs."""
    ids: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if "-" in token:
            lo, _, hi = token.partition("-")
            ids.update(range(int(lo), int(hi) + 1))
        elif token.isdigit():
            ids.add(int(token))
    return ids


def collect_vlans(task: Task) -> Result:
    task.run(task=napalm_get, getters=["vlans"])
    raw = task.results[1].result.get("vlans", {})
    rows = []
    for vid_str, info in raw.items():
        ifaces = info.get("interfaces", [])
        rows.append(
            {
                "id": int(vid_str),
                "name": info.get("name", ""),
                "interfaces": sorted(ifaces),
                "ghost": len(ifaces) == 0,
            }
        )
    rows.sort(key=lambda r: r["id"])
    return Result(host=task.host, result=rows)


def _row(values) -> str:
    return "  ".join(str(v).ljust(w) for v, w in zip(values, _COL_W))


def render_table(hostname: str, rows: list[dict]) -> str:
    buf = StringIO()
    buf.write(f"\n{'=' * len(_SEP)}\n{hostname}\n{_SEP}\n")
    buf.write(_row(_HEADERS) + "\n")
    buf.write(_SEP + "\n")
    for r in rows:
        iface_str = ", ".join(r["interfaces"]) or "(none)"
        ghost_flag = "[!]" if r["ghost"] else ""
        buf.write(_row((r["id"], r["name"], ghost_flag, iface_str)) + "\n")
    active = sum(1 for r in rows if not r["ghost"])
    buf.write(f"{_SEP}\n{len(rows)} VLANs total, {active} active, "
              f"{len(rows) - active} ghost\n")
    return buf.getvalue()


def write_csv(all_rows: list[dict], path: str) -> None:
    fields = ["host", "id", "name", "ghost", "interfaces"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(all_rows, key=lambda r: (r["host"], r["id"])):
            writer.writerow({**row, "interfaces": "|".join(row["interfaces"])})
    logger.info("CSV written to %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect and report VLAN databases from network switches via NAPALM"
    )
    parser.add_argument("--hosts", default="inventory/hosts.yaml", metavar="FILE")
    parser.add_argument("--groups", default="inventory/groups.yaml", metavar="FILE")
    parser.add_argument("--defaults", default="inventory/defaults.yaml", metavar="FILE")
    parser.add_argument(
        "--filter", metavar="KEY=VAL[,KEY=VAL]",
        help="Nornir host filter expression (e.g. role=access,site=nyc)",
    )
    parser.add_argument(
        "--vlan-id", metavar="SPEC",
        help="Restrict output to these VLANs (e.g. 10,20,100-199)",
    )
    parser.add_argument(
        "--ghost-only", action="store_true",
        help="Show only ghost VLANs (defined but no active interfaces)",
    )
    parser.add_argument(
        "--format", choices=["table", "json", "csv"], default="table",
        help="Output format (default: table)",
    )
    parser.add_argument("--output", metavar="FILE", help="Write output to FILE")
    parser.add_argument("--workers", type=int, default=10, metavar="N",
                        help="Parallel worker threads (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    vlan_ids = parse_vlan_filter(args.vlan_id) if args.vlan_id else set()

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

    logger.info("Polling VLAN database on %d host(s)...", len(nr.inventory.hosts))
    nr_results = nr.run(task=collect_vlans, name="collect_vlans")

    report: dict[str, list[dict]] = {}
    flat_rows: list[dict] = []
    any_failed = False

    for host, multi_result in nr_results.items():
        if multi_result.failed:
            logger.error("%-20s  FAILED — %s", host, multi_result.exception)
            any_failed = True
            continue

        rows: list[dict] = multi_result[0].result
        if vlan_ids:
            rows = [r for r in rows if r["id"] in vlan_ids]
        if args.ghost_only:
            rows = [r for r in rows if r["ghost"]]

        report[host] = rows
        for r in rows:
            flat_rows.append({"host": host, **r})

    if args.format == "table":
        output = "".join(render_table(h, rows) for h, rows in sorted(report.items()))
    elif args.format == "json":
        output = json.dumps(
            {h: rows for h, rows in sorted(report.items())}, indent=2
        )
    else:
        if args.output:
            write_csv(flat_rows, args.output)
            sys.exit(1 if any_failed else 0)
        output = ""
        logger.error("--format csv requires --output FILE")
        sys.exit(2)

    if args.output:
        Path(args.output).write_text(output)
        logger.info("Report written to %s", args.output)
    else:
        print(output)

    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()