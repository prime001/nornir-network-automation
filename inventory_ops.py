The numbered scripts are in the GitHub repo, not locally. I have the structure from the task description. Writing the script now — outputting only the content as requested.

```
009_inventory_validator.py — inventory validation and enrichment using live NAPALM facts
```

"""
009_inventory_validator.py - Inventory Validation and Enrichment

Purpose:
    Connects to every host in the Nornir inventory, retrieves live NAPALM facts
    (hostname, OS version, serial number, model, uptime), and cross-checks them
    against what the inventory file declares.  Flags hostname mismatches, exports
    enriched records to CSV or JSON, and optionally exits non-zero when drift is
    detected — suitable for CI pipelines or scheduled audits.

Usage:
    python 009_inventory_validator.py
    python 009_inventory_validator.py --groups core distribution
    python 009_inventory_validator.py --export-csv enriched.csv --export-json enriched.json
    python 009_inventory_validator.py --fail-on-mismatch

Prerequisites:
    pip install nornir nornir-napalm napalm
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml
    Credentials set in inventory data or via NORNIR_DEFAULT_USERNAME /
    NORNIR_DEFAULT_PASSWORD environment variables.
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("inventory_validator")


def _collect_facts(task: Task) -> Result:
    """Fetch live device facts and validate against declared inventory values."""
    getters_result = task.run(task=napalm_get, getters=["facts"])
    facts = getters_result[0].result["facts"]

    declared_hostname = task.host.name
    actual_hostname = facts.get("hostname", "")

    mismatches = []
    if actual_hostname.lower() != declared_hostname.lower():
        mismatches.append(
            f"hostname declared='{declared_hostname}' actual='{actual_hostname}'"
        )

    groups = [g.name for g in task.host.groups] if task.host.groups else []

    record = {
        "host": declared_hostname,
        "ip": str(task.host.hostname),
        "groups": ",".join(groups),
        "declared_platform": task.host.platform or "",
        "actual_hostname": actual_hostname,
        "os_version": facts.get("os_version", ""),
        "model": facts.get("model", ""),
        "serial_number": facts.get("serial_number", ""),
        "uptime_seconds": facts.get("uptime", 0),
        "mismatches": mismatches,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    return Result(host=task.host, result=record)


def _export_csv(records: list, path: str) -> None:
    if not records:
        return
    fieldnames = list(records[0].keys())
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            flat = {k: ("; ".join(v) if isinstance(v, list) else v) for k, v in rec.items()}
            writer.writerow(flat)
    logger.info("CSV written → %s", path)


def _export_json(records: list, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(records, fh, indent=2)
    logger.info("JSON written → %s", path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Nornir inventory entries against live device facts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", default="nornir.yaml",
        help="Path to Nornir config file (default: nornir.yaml)",
    )
    parser.add_argument(
        "--groups", nargs="+", metavar="GROUP",
        help="Restrict validation to one or more inventory groups",
    )
    parser.add_argument("--export-csv", metavar="FILE", help="Write enriched records to a CSV file")
    parser.add_argument("--export-json", metavar="FILE", help="Write enriched records to a JSON file")
    parser.add_argument(
        "--fail-on-mismatch", action="store_true",
        help="Exit with code 1 if any hostname mismatches are detected",
    )
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Number of parallel workers (default: 10)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(config_file=args.config)
    nr.config.runner.options["num_workers"] = args.workers

    if args.groups:
        nr = nr.filter(F(groups__any=args.groups))

    total = len(nr.inventory.hosts)
    logger.info("Validating %d host(s)", total)

    agg = nr.run(task=_collect_facts, name="collect_facts")

    enriched: list = []
    mismatched: list = []
    failed: list = []

    for host, multi in agg.items():
        if multi.failed:
            logger.error("FAILED  %-20s  %s", host, multi[0].exception)
            failed.append(host)
            continue

        rec = multi[0].result
        enriched.append(rec)

        if rec["mismatches"]:
            mismatched.append(host)
            for detail in rec["mismatches"]:
                logger.warning("MISMATCH %-20s  %s", host, detail)
        else:
            logger.info(
                "OK      %-20s  serial=%-16s  os=%s",
                host, rec["serial_number"], rec["os_version"],
            )

    ok_count = len(enriched) - len(mismatched)
    print("\n─── Inventory Validation Summary ──────────────────────────")
    print(f"  Total hosts     : {total}")
    print(f"  Validated OK    : {ok_count}")
    print(f"  Mismatched      : {len(mismatched)}")
    print(f"  Failed / timeout: {len(failed)}")
    if mismatched:
        print(f"  Mismatch list   : {', '.join(mismatched)}")
    if failed:
        print(f"  Failed list     : {', '.join(failed)}")
    print("────────────────────────────────────────────────────────────\n")

    if args.export_csv:
        _export_csv(enriched, args.export_csv)
    if args.export_json:
        _export_json(enriched, args.export_json)

    if args.fail_on_mismatch and mismatched:
        sys.exit(1)


if __name__ == "__main__":
    main()