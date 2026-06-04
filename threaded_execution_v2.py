I have enough context from the prompt's file list. Writing the script now.

```python
"""
ntp_audit.py - NTP Synchronization Audit via Nornir

Purpose:
    Connects to network devices in parallel and audits NTP synchronization
    status. Reports stratum level, reference clock, and sync state per device.
    Useful for identifying time-drift issues before they cause certificate
    validation failures or cross-device log correlation problems.

Usage:
    python ntp_audit.py [options]

    # Audit all inventory devices with 10 parallel workers
    python ntp_audit.py

    # Target specific hosts or groups
    python ntp_audit.py --hosts core-sw-01 core-sw-02
    python ntp_audit.py --groups datacenter --workers 20

    # Export results
    python ntp_audit.py --output json --out-file ntp_report.json
    python ntp_audit.py --output csv --out-file ntp_report.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files in the working directory: hosts.yaml, groups.yaml, defaults.yaml
    Devices must support 'show ntp status' (IOS, IOS-XE, NX-OS).
"""

import argparse
import csv
import json
import logging
import sys
from io import StringIO
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
log = logging.getLogger(__name__)


def _parse_ntp_status(output: str) -> dict[str, Any]:
    """Extract sync state, stratum, and reference from 'show ntp status'."""
    info: dict[str, Any] = {
        "synchronized": False,
        "stratum": None,
        "reference": None,
    }
    for line in output.splitlines():
        lower = line.lower()
        if "clock is synchronized" in lower:
            info["synchronized"] = True
        elif "clock is unsynchronized" in lower:
            info["synchronized"] = False
        if "stratum" in lower:
            parts = line.split()
            for i, part in enumerate(parts):
                if part.lower() == "stratum" and i + 1 < len(parts):
                    try:
                        info["stratum"] = int(parts[i + 1].rstrip(".,"))
                    except ValueError:
                        pass
        if "reference is" in lower:
            tail = line.split("reference is", 1)[1].strip()
            info["reference"] = tail.split()[0] if tail else None
    return info


def audit_ntp(task: Task) -> Result:
    """Nornir task: collect and parse NTP status from one device."""
    r = task.run(
        task=netmiko_send_command,
        command_string="show ntp status",
        name="ntp_status",
    )
    parsed = _parse_ntp_status(r.result)
    state = "SYNCED" if parsed["synchronized"] else "UNSYNCED"
    diff = f"{state} | stratum={parsed['stratum']} | ref={parsed['reference']}"
    return Result(host=task.host, result=parsed, changed=False, diff=diff)


def _apply_filter(nr, hosts: list[str], groups: list[str]):
    if hosts:
        return nr.filter(F(name__any=hosts))
    if groups:
        return nr.filter(F(groups__any=groups))
    return nr


def _render_table(records: list[dict]) -> str:
    headers = ["Host", "Synced", "Stratum", "Reference"]
    rows = [
        [
            r["host"],
            "YES" if r["synchronized"] else "NO",
            str(r["stratum"] or "-"),
            r["reference"] or "-",
        ]
        for r in records
    ]
    widths = [
        max(len(h), max((len(row[i]) for row in rows), default=0))
        for i, h in enumerate(headers)
    ]
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"
    lines = [sep, fmt.format(*headers), sep]
    for row in rows:
        lines.append(fmt.format(*row))
    lines.append(sep)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit NTP synchronization status across network devices in parallel."
    )
    parser.add_argument("--hosts", nargs="+", metavar="HOST",
                        help="Limit audit to these hostnames")
    parser.add_argument("--groups", nargs="+", metavar="GROUP",
                        help="Limit audit to these inventory groups")
    parser.add_argument("--workers", type=int, default=10, metavar="N",
                        help="Thread pool size (default: 10)")
    parser.add_argument("--output", choices=["table", "json", "csv"],
                        default="table", help="Output format (default: table)")
    parser.add_argument("--out-file", metavar="FILE",
                        help="Write output to FILE instead of stdout")
    parser.add_argument("--config", default="config.yaml", metavar="FILE",
                        help="Nornir config file (default: config.yaml)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print raw nornir task output")
    args = parser.parse_args()

    try:
        nr = InitNornir(
            config_file=args.config,
            runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        )
    except Exception as exc:
        log.error("Failed to initialize Nornir: %s", exc)
        return 1

    target = _apply_filter(nr, args.hosts or [], args.groups or [])
    if not target.inventory.hosts:
        print("No hosts matched the specified filter.", file=sys.stderr)
        return 1

    print(
        f"Auditing NTP on {len(target.inventory.hosts)} device(s) "
        f"with {args.workers} worker(s)...",
        file=sys.stderr,
    )

    results = target.run(task=audit_ntp, name="NTP Audit")

    if args.verbose:
        print_result(results)

    records = []
    for host, mr in results.items():
        if mr.failed:
            log.warning("Failed to audit %s: %s", host, mr.exception)
            records.append({
                "host": host,
                "synchronized": None,
                "stratum": None,
                "reference": None,
                "error": str(mr.exception),
            })
        else:
            data = {**mr.result, "host": host, "error": None}
            records.append(data)

    ok_records = [r for r in records if r["error"] is None]
    unsynced = [r["host"] for r in ok_records if not r["synchronized"]]
    failed = [r["host"] for r in records if r["error"] is not None]

    if args.output == "json":
        out = json.dumps(records, indent=2)
    elif args.output == "csv":
        buf = StringIO()
        writer = csv.DictWriter(
            buf, fieldnames=["host", "synchronized", "stratum", "reference", "error"]
        )
        writer.writeheader()
        writer.writerows(records)
        out = buf.getvalue()
    else:
        out = _render_table(ok_records) if ok_records else "(no reachable devices)"
        if failed:
            out += f"\n\nFailed ({len(failed)}): {', '.join(failed)}"

    if args.out_file:
        with open(args.out_file, "w") as fh:
            fh.write(out)
        print(f"Results written to {args.out_file}", file=sys.stderr)
    else:
        print(out)

    if unsynced:
        print(
            f"\nWARNING: {len(unsynced)} device(s) not synchronized: {', '.join(unsynced)}",
            file=sys.stderr,
        )
    if failed:
        print(
            f"ERROR: {len(failed)} device(s) unreachable.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
```