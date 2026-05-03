```python
"""
020_custom_plugins.py — Custom Nornir Processor and Inventory Transform Plugins

Purpose:
    Demonstrates building reusable Nornir plugin components beyond simple tasks:
    a custom IResultsProcessor that streams results to structured JSON/CSV reports,
    and a composite health-check task that aggregates NAPALM environment, facts,
    and interface-error data into a single audit snapshot per device.

Usage:
    python 020_custom_plugins.py
    python 020_custom_plugins.py --hosts spine1,leaf2 --format csv
    python 020_custom_plugins.py --filter role=core --output-dir ./reports
    python 020_custom_plugins.py --dry-run        # validate inventory only

Prerequisites:
    pip install nornir nornir-napalm napalm
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml in ./inventory/
    Each host must carry 'platform' (eos, ios, junos, nxos_ssh, etc.)
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.inventory import Host
from nornir.core.task import AggregatedResult, MultiResult, Result, Task
from nornir_napalm.plugins.tasks import napalm_get

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom processor plugin — implements Nornir's IResultsProcessor interface
# ---------------------------------------------------------------------------

class StructuredExportProcessor:
    """
    Collects per-host task results during a Nornir run and flushes them
    to JSON and/or CSV on demand.  Wire it in via nr.with_processors([...]).
    """

    def __init__(self, output_dir: Path, formats: List[str]) -> None:
        self.output_dir = output_dir
        self.formats = formats
        self._rows: List[Dict[str, Any]] = []
        output_dir.mkdir(parents=True, exist_ok=True)

    # IResultsProcessor interface -------------------------------------------

    def task_started(self, task: Task) -> None:
        log.debug("task started: %s", task.name)

    def task_completed(self, task: Task, result: AggregatedResult) -> None:
        log.debug("task completed: %s  failed_hosts=%d", task.name,
                  sum(1 for r in result.values() if r.failed))

    def task_instance_started(self, task: Task, host: Host) -> None:
        pass

    def task_instance_completed(
        self, task: Task, host: Host, result: MultiResult
    ) -> None:
        row: Dict[str, Any] = {
            "host": host.name,
            "platform": host.platform or "unknown",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "failed": result.failed,
        }
        if not result.failed and result[0].result:
            row.update(self._flatten(result[0].result))
        self._rows.append(row)

    def subtask_instance_started(self, task: Task, host: Host) -> None:
        pass

    def subtask_instance_completed(
        self, task: Task, host: Host, result: MultiResult
    ) -> None:
        pass

    # Helpers ---------------------------------------------------------------

    @staticmethod
    def _flatten(data: Any, prefix: str = "") -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"result": str(data)}
        out: Dict[str, Any] = {}
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(StructuredExportProcessor._flatten(v, key))
            elif isinstance(v, list):
                out[key] = ", ".join(str(i) for i in v)
            else:
                out[key] = v
        return out

    def flush(self, stem: str = "health_report") -> List[Path]:
        """Write buffered rows to disk; returns list of paths created."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        written: List[Path] = []

        if "json" in self.formats:
            path = self.output_dir / f"{stem}_{ts}.json"
            path.write_text(json.dumps(self._rows, indent=2, default=str))
            written.append(path)
            log.info("wrote JSON: %s", path)

        if "csv" in self.formats:
            path = self.output_dir / f"{stem}_{ts}.csv"
            all_keys = sorted({k for row in self._rows for k in row})
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=all_keys,
                                        extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self._rows)
            written.append(path)
            log.info("wrote CSV: %s", path)

        return written


# ---------------------------------------------------------------------------
# Custom composite task
# ---------------------------------------------------------------------------

def device_health_snapshot(task: Task) -> Result:
    """
    Single Nornir task that pulls facts, environment, and interface counters
    via NAPALM and returns a unified health dict — failed interfaces and
    thermal alerts included.
    """
    r = task.run(
        napalm_get,
        getters=["facts", "environment", "interfaces_counters"],
        name="napalm_get_health_bundle",
    )
    if r.failed:
        return Result(host=task.host, result=None, failed=True)

    raw = r[0].result
    facts = raw.get("facts", {})
    env = raw.get("environment", {})
    counters = raw.get("interfaces_counters", {})

    errored = [
        iface for iface, s in counters.items()
        if s.get("rx_errors", 0) > 0 or s.get("tx_errors", 0) > 0
    ]
    temp_alerts = [
        sensor for sensor, d in env.get("temperature", {}).items()
        if isinstance(d, dict) and d.get("is_alert", False)
    ]

    return Result(
        host=task.host,
        result={
            "hostname": facts.get("hostname", task.host.name),
            "vendor": facts.get("vendor", "unknown"),
            "model": facts.get("model", "unknown"),
            "os_version": facts.get("os_version", "unknown"),
            "uptime_seconds": facts.get("uptime", 0),
            "interface_count": len(counters),
            "errored_interfaces": errored,
            "temperature_alerts": temp_alerts,
            "fans_ok": all(
                v.get("status", False)
                for v in env.get("fans", {}).values()
                if isinstance(v, dict)
            ),
            "power_ok": all(
                v.get("status", False)
                for v in env.get("power", {}).values()
                if isinstance(v, dict)
            ),
        },
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Custom Nornir processor plugin — structured device health export"
    )
    p.add_argument("--config", default="nornir.yaml")
    p.add_argument("--hosts", help="Comma-separated host names")
    p.add_argument("--filter", metavar="KEY=VAL",
                   help="Inventory data filter, e.g. role=spine")
    p.add_argument("--output-dir", default="./reports")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--dry-run", action="store_true",
                   help="Print matched hosts without connecting")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def build_nornir(args: argparse.Namespace) -> Nornir:
    nr = InitNornir(config_file=args.config, logging={"enabled": False})
    if args.hosts:
        targets = set(args.hosts.split(","))
        nr = nr.filter(filter_func=lambda h: h.name in targets)
    if args.filter:
        key, _, val = args.filter.partition("=")
        nr = nr.filter(**{key: val})
    nr.config.runner.options["num_workers"] = args.workers
    return nr


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        nr = build_nornir(args)
    except FileNotFoundError as exc:
        log.error("nornir config not found: %s", exc)
        return 1

    hosts = list(nr.inventory.hosts)
    if not hosts:
        log.error("no hosts matched — check --hosts or --filter")
        return 1

    log.info("targeting %d host(s): %s", len(hosts), ", ".join(hosts))

    if args.dry_run:
        log.info("dry-run: exiting without connecting")
        return 0

    formats = ["json", "csv"] if args.format == "both" else [args.format]
    exporter = StructuredExportProcessor(
        output_dir=Path(args.output_dir),
        formats=formats,
    )

    result = nr.with_processors([exporter]).run(
        task=device_health_snapshot,
        name="device_health_snapshot",
    )

    failed = [h for h, r in result.items() if r.failed]
    if failed:
        log.warning("failed hosts (%d): %s", len(failed), ", ".join(failed))

    written = exporter.flush()
    log.info("reports: %s", [str(p) for p in written])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```