The script goes to the portfolio repo (nornir-network-automation), not this local repo. Writing a config drift detection script now:

```python
"""
config_diff.py - Configuration drift detection using Nornir.

Connects to network devices, retrieves running configurations, and
compares them against stored baseline snapshots. Reports any lines added,
removed, or changed since the baseline was captured.  Useful for detecting
unauthorized changes, validating change windows, and maintaining audit trails.

Usage:
    # First run — create baselines (no drift check yet):
    python config_diff.py --update-baseline

    # Check for drift against saved baselines:
    python config_diff.py

    # Limit to specific hosts or groups, write report to file:
    python config_diff.py --hosts router1 router2 --output drift.txt
    python config_diff.py --groups core --update-baseline

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: hosts.yaml, groups.yaml, defaults.yaml (or config.yaml)
    Python 3.10+
"""

import argparse
import difflib
import logging
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("config_diff")


def _load_baseline(baseline_dir: Path, hostname: str) -> str | None:
    path = baseline_dir / f"{hostname}.cfg"
    return path.read_text(encoding="utf-8") if path.exists() else None


def _save_baseline(baseline_dir: Path, hostname: str, config: str) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / f"{hostname}.cfg").write_text(config, encoding="utf-8")
    logger.info("[%s] Baseline saved.", hostname)


def _unified_diff(baseline: str, running: str, hostname: str) -> list[str]:
    return list(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            running.splitlines(keepends=True),
            fromfile=f"{hostname}/baseline",
            tofile=f"{hostname}/running",
            lineterm="",
        )
    )


def detect_drift(task: Task, baseline_dir: Path, update_baseline: bool) -> Result:
    hostname = task.host.name

    cmd_result = task.run(
        task=netmiko_send_command,
        command_string="show running-config",
        use_textfsm=False,
    )
    running = cmd_result.result.strip()

    baseline = _load_baseline(baseline_dir, hostname)

    if baseline is None:
        if update_baseline:
            _save_baseline(baseline_dir, hostname, running)
            return Result(host=task.host, result="NO_BASELINE_CREATED")
        return Result(
            host=task.host,
            result="NO_BASELINE",
            failed=True,
        )

    diff_lines = _unified_diff(baseline.strip(), running, hostname)

    if diff_lines and update_baseline:
        _save_baseline(baseline_dir, hostname, running)

    return Result(
        host=task.host,
        result={"diff": diff_lines, "running": running},
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect configuration drift between running and baseline configs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--hosts", nargs="+", metavar="HOST", help="Filter by hostname")
    p.add_argument("--groups", nargs="+", metavar="GROUP", help="Filter by group")
    p.add_argument(
        "--baseline-dir", default="./baselines", metavar="DIR",
        help="Directory for baseline config files",
    )
    p.add_argument(
        "--update-baseline", action="store_true",
        help="Write running config as new baseline (creates on first run, updates on drift)",
    )
    p.add_argument("--output", metavar="FILE", help="Write report to file")
    p.add_argument("--username", help="Override inventory username")
    p.add_argument("--password", help="Override inventory password")
    p.add_argument("--workers", type=int, default=10, help="Parallel worker count")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    baseline_dir = Path(args.baseline_dir)

    try:
        nr = InitNornir(config_file=args.config)
    except FileNotFoundError:
        logger.error("Nornir config not found: %s", args.config)
        return 1

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    nr.runner.num_workers = args.workers

    if args.hosts:
        nr = nr.filter(F(name__any=args.hosts))
    if args.groups:
        nr = nr.filter(F(groups__any=args.groups))

    if not nr.inventory.hosts:
        logger.error("No hosts matched filters.")
        return 1

    logger.info(
        "Checking %d host(s) against baselines in %s",
        len(nr.inventory.hosts),
        baseline_dir,
    )

    results = nr.run(
        task=detect_drift,
        baseline_dir=baseline_dir,
        update_baseline=args.update_baseline,
    )

    drifted, clean, failed, created = [], [], [], []
    lines = [
        f"Config Drift Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
    ]

    for hostname, multi in results.items():
        r = multi[0]
        if r.failed:
            if r.result == "NO_BASELINE":
                lines.append(
                    f"\n[SKIP] {hostname}: no baseline — run with --update-baseline"
                )
                failed.append(hostname)
            else:
                lines.append(f"\n[ERROR] {hostname}: {r.exception or r.result}")
                failed.append(hostname)
        elif r.result == "NO_BASELINE_CREATED":
            lines.append(f"\n[INIT] {hostname}: baseline created")
            created.append(hostname)
        else:
            diff = r.result["diff"]
            if diff:
                lines.append(f"\n[DRIFT] {hostname} — {len(diff)} changed lines:")
                lines.extend(diff)
                drifted.append(hostname)
            else:
                lines.append(f"\n[CLEAN] {hostname}: matches baseline")
                clean.append(hostname)

    lines.append("\n" + "=" * 60)
    lines.append(
        f"Summary: {len(clean)} clean, {len(drifted)} drifted, "
        f"{len(created)} initialized, {len(failed)} failed"
    )
    if drifted:
        lines.append(f"Drifted: {', '.join(drifted)}")
    if failed:
        lines.append(f"Failed:  {', '.join(failed)}")

    report = "\n".join(lines)
    print(report)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        logger.info("Report written to %s", args.output)

    return 1 if (drifted or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
```