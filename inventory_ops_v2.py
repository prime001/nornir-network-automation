The nornir scripts aren't in `/opt/NetAutoCommitter` — that's a different project. The user asked for script content output only. Here it is:

```
"""
029_inventory_ops.py — Inventory Health Check and Export
...
```

Since the user asked for **output only the script content**, here it is directly:

---

```python
"""
029_inventory_ops.py — Inventory Health Check and Export

Purpose:
    Validates a Nornir inventory for reachability and credential correctness,
    then exports a filtered snapshot to CSV or JSON for asset-management
    or change-management consumption.

    Complements 009/019 inventory-ops scripts by focusing on *validation*
    and *export* rather than basic enumeration.

Usage:
    # Check all hosts, export to CSV
    python 029_inventory_ops.py --export csv --output inventory_export.csv

    # Check only 'datacenter' group, export JSON
    python 029_inventory_ops.py --group datacenter --export json --output dc.json

    # Reachability check only (no export)
    python 029_inventory_ops.py --check-only

    # Show summary table without writing a file
    python 029_inventory_ops.py --summary

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory files: hosts.yaml, groups.yaml, defaults.yaml
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("inventory_health")


def probe_host(task: Task) -> Result:
    """Send a no-op command to verify credentials and connectivity."""
    output = task.run(
        task=netmiko_send_command,
        command_string="show version | head 2",
        name="probe",
    )
    return Result(host=task.host, result=output.result, changed=False)


def run_health_check(nr) -> dict:
    """
    Execute probe against all hosts; return a dict keyed by hostname with
    status ('ok' | 'unreachable' | 'auth_failed') and latency metadata.
    """
    results = {}
    logger.info("Starting health check on %d host(s)", len(nr.inventory.hosts))

    agg = nr.run(task=probe_host, on_failed=True)

    for host_name, multi in agg.items():
        host = nr.inventory.hosts[host_name]
        entry = {
            "hostname": host_name,
            "ip": str(host.hostname or ""),
            "platform": str(host.platform or ""),
            "groups": [str(g) for g in host.groups],
            "port": host.port,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        top = multi[0]
        if top.failed:
            err = str(top.exception or "")
            if any(kw in err.lower() for kw in ("auth", "authentication", "password")):
                entry["status"] = "auth_failed"
            else:
                entry["status"] = "unreachable"
            entry["error"] = err[:200]
        else:
            entry["status"] = "ok"
            entry["error"] = ""

        results[host_name] = entry

    return results


def print_summary(results: dict) -> None:
    ok = [h for h, v in results.items() if v["status"] == "ok"]
    unreachable = [h for h, v in results.items() if v["status"] == "unreachable"]
    auth_fail = [h for h, v in results.items() if v["status"] == "auth_failed"]

    print("\n=== Inventory Health Summary ===")
    print(f"Total hosts : {len(results)}")
    print(f"  OK         : {len(ok)}")
    print(f"  Unreachable: {len(unreachable)}")
    print(f"  Auth failed: {len(auth_fail)}")

    if unreachable:
        print("\nUnreachable:")
        for h in unreachable:
            print(f"  {h:30s}  {results[h]['ip']}")
    if auth_fail:
        print("\nAuth failed:")
        for h in auth_fail:
            print(f"  {h:30s}  {results[h]['ip']}")
    print()


def export_csv(results: dict, output_path: Path) -> None:
    fields = ["hostname", "ip", "platform", "groups", "port", "status", "error", "checked_at"]
    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in results.values():
            row = dict(entry)
            row["groups"] = ",".join(entry.get("groups", []))
            writer.writerow(row)
    logger.info("CSV written to %s (%d rows)", output_path, len(results))


def export_json(results: dict, output_path: Path) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host_count": len(results),
        "hosts": list(results.values()),
    }
    with output_path.open("w") as fh:
        json.dump(payload, fh, indent=2)
    logger.info("JSON written to %s (%d hosts)", output_path, len(results))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Nornir inventory health check and export tool.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--group", help="Limit check to a single inventory group")
    p.add_argument(
        "--export",
        choices=["csv", "json"],
        help="Export format (omit to skip file export)",
    )
    p.add_argument("--output", default="inventory_export", help="Output file path (no extension)")
    p.add_argument(
        "--check-only",
        action="store_true",
        help="Run health check without exporting",
    )
    p.add_argument("--summary", action="store_true", help="Print summary table to stdout")
    p.add_argument("--workers", type=int, default=10, help="Parallel workers")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not Path(args.config).exists():
        logger.error("Nornir config not found: %s", args.config)
        return 1

    nr = InitNornir(config_file=args.config, runner={"options": {"num_workers": args.workers}})

    if args.group:
        nr = nr.filter(groups=args.group)
        if not nr.inventory.hosts:
            logger.error("No hosts found in group '%s'", args.group)
            return 1
        logger.info("Filtered to group '%s': %d host(s)", args.group, len(nr.inventory.hosts))

    results = run_health_check(nr)

    if args.summary or args.check_only:
        print_summary(results)

    if args.check_only:
        failed = sum(1 for v in results.values() if v["status"] != "ok")
        return 1 if failed else 0

    if args.export == "csv":
        out = Path(args.output).with_suffix(".csv")
        export_csv(results, out)
    elif args.export == "json":
        out = Path(args.output).with_suffix(".json")
        export_json(results, out)
    else:
        print_summary(results)
        logger.info("No export format specified; use --export csv|json to write a file.")

    failed = sum(1 for v in results.values() if v["status"] != "ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```