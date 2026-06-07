```python
"""
config_drift.py - Detect configuration drift against saved baselines.

Purpose:
    Compares each device's current running configuration against a previously
    saved baseline, reporting any lines added or removed since the snapshot.
    Useful for change-window auditing, unauthorized-change detection, and
    pre/post-maintenance diffs.

Usage:
    python config_drift.py --inventory hosts.yaml --baseline-dir ./baselines
    python config_drift.py --inventory hosts.yaml --baseline-dir ./baselines \
        --filter role=core --fail-on-drift

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Baseline files must be named <hostname>.txt and live in --baseline-dir.
    Generate them first with config_backup.py or any show-run capture.
"""

import argparse
import difflib
import logging
import sys
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _load_baseline(hostname: str, baseline_dir: Path) -> list[str] | None:
    path = baseline_dir / f"{hostname}.txt"
    if not path.exists():
        return None
    return path.read_text().splitlines(keepends=True)


def detect_drift(task: Task, baseline_dir: Path, context_lines: int) -> Result:
    hostname = task.host.name
    baseline = _load_baseline(hostname, baseline_dir)
    if baseline is None:
        return Result(
            host=task.host,
            result=f"No baseline file found for {hostname}",
            failed=False,
            changed=False,
        )

    cmd = task.host.get("show_run_cmd", "show running-config")
    r = task.run(netmiko_send_command, command_string=cmd, use_textfsm=False)
    current = r.result.splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            baseline,
            current,
            fromfile=f"{hostname}/baseline",
            tofile=f"{hostname}/current",
            n=context_lines,
        )
    )

    if diff:
        return Result(
            host=task.host,
            result="".join(diff),
            failed=False,
            changed=True,
        )
    return Result(host=task.host, result="No drift detected", failed=False, changed=False)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect config drift between running configs and saved baselines."
    )
    p.add_argument("--inventory", required=True, help="Nornir hosts YAML file")
    p.add_argument(
        "--baseline-dir",
        required=True,
        type=Path,
        help="Directory containing <hostname>.txt baseline files",
    )
    p.add_argument(
        "--filter",
        metavar="KEY=VALUE",
        action="append",
        dest="filters",
        default=[],
        help="Filter hosts by data field, e.g. --filter role=core",
    )
    p.add_argument(
        "--context",
        type=int,
        default=3,
        metavar="N",
        help="Lines of context around each diff hunk (default: 3)",
    )
    p.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit with status 1 if any host has drift (useful for CI gates)",
    )
    p.add_argument("--username", help="Override inventory username")
    p.add_argument("--password", help="Override inventory password")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    return p.parse_args()


def build_filter(filters: list[str]) -> F | None:
    if not filters:
        return None
    combined = None
    for kv in filters:
        key, _, value = kv.partition("=")
        f = F(**{key: value})
        combined = f if combined is None else combined & f
    return combined


if __name__ == "__main__":
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.baseline_dir.is_dir():
        log.error("Baseline directory does not exist: %s", args.baseline_dir)
        sys.exit(2)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 10}},
        inventory={"plugin": "SimpleInventory", "options": {"host_file": args.inventory}},
        logging={"enabled": False},
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    host_filter = build_filter(args.filters)
    if host_filter:
        nr = nr.filter(host_filter)

    if not nr.inventory.hosts:
        log.error("No hosts matched the supplied filters.")
        sys.exit(2)

    log.info("Checking drift on %d host(s)...", len(nr.inventory.hosts))
    results = nr.run(
        task=detect_drift,
        name="config_drift",
        baseline_dir=args.baseline_dir,
        context_lines=args.context,
    )

    print_result(results)

    drifted = [h for h, mr in results.items() if mr.changed]
    failed = [h for h, mr in results.items() if mr.failed]

    if failed:
        log.warning("%d host(s) failed: %s", len(failed), ", ".join(failed))
    if drifted:
        log.warning("Drift detected on %d host(s): %s", len(drifted), ", ".join(drifted))
    else:
        log.info("All reachable hosts match their baselines.")

    if args.fail_on_drift and drifted:
        sys.exit(1)
```