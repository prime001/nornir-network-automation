config_drift.py — Configuration drift detection using Nornir

Purpose:
    Compares live device running configurations against stored baseline snapshots,
    producing unified diffs that highlight unauthorized or unexpected changes.
    Designed for change auditing, incident response triage, and post-maintenance
    verification across multi-vendor environments.

Usage:
    # Check all hosts for drift against stored baselines
    python config_drift.py --baseline-dir ./baselines

    # Target specific hosts
    python config_drift.py --hosts router1,router2 --baseline-dir ./baselines

    # Target a Nornir group and update baselines after checking
    python config_drift.py --group core_routers --baseline-dir ./baselines --update

    # Create initial baselines (no existing baseline required)
    python config_drift.py --baseline-dir ./baselines --update

Prerequisites:
    pip install nornir nornir-netmiko
    Inventory files: ./inventory/hosts.yaml, ./inventory/groups.yaml, ./inventory/defaults.yaml
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
)
logger = logging.getLogger(__name__)


def fetch_running_config(task: Task) -> Result:
    result = task.run(
        task=netmiko_send_command,
        command_string="show running-config",
        use_textfsm=False,
    )
    return Result(host=task.host, result=result.result)


def load_baseline(baseline_dir: Path, hostname: str) -> str | None:
    path = baseline_dir / f"{hostname}.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def save_baseline(baseline_dir: Path, hostname: str, config: str) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / f"{hostname}.txt").write_text(config, encoding="utf-8")
    logger.info("[%s] Baseline saved to %s/%s.txt", hostname, baseline_dir, hostname)


def compute_diff(baseline: str, current: str, hostname: str) -> list[str]:
    return list(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"{hostname} (baseline)",
            tofile=f"{hostname} (current {datetime.now().strftime('%Y-%m-%d %H:%M')})",
            lineterm="",
        )
    )


def run(args: argparse.Namespace) -> int:
    baseline_dir = Path(args.baseline_dir)
    nr = InitNornir(config_file=args.config)

    if args.hosts:
        target = nr.filter(F(name__in=args.hosts.split(",")))
    elif args.group:
        target = nr.filter(F(groups__contains=args.group))
    else:
        target = nr

    host_count = len(target.inventory.hosts)
    if host_count == 0:
        logger.error("No hosts matched the filter.")
        return 1

    logger.info("Fetching running configs from %d host(s)...", host_count)
    results = target.run(task=fetch_running_config)

    drifted: list[str] = []
    no_baseline: list[str] = []

    for hostname, multi_result in results.items():
        if multi_result.failed:
            exc = multi_result[0].exception
            logger.error("[%s] Connection failed: %s", hostname, exc)
            continue

        current_config: str = multi_result[0].result
        baseline = load_baseline(baseline_dir, hostname)

        if baseline is None:
            no_baseline.append(hostname)
            if args.update:
                save_baseline(baseline_dir, hostname, current_config)
            else:
                logger.warning(
                    "[%s] No baseline found. Run with --update to create one.", hostname
                )
            continue

        diff = compute_diff(baseline, current_config, hostname)

        if not diff:
            logger.info("[%s] Clean — no drift detected.", hostname)
        else:
            drifted.append(hostname)
            separator = "=" * 64
            print(f"\n{separator}")
            print(f"  DRIFT DETECTED: {hostname}")
            print(separator)
            for line in diff:
                print(line)

        if args.update:
            save_baseline(baseline_dir, hostname, current_config)

    print()
    if drifted:
        logger.warning("Drift found on %d host(s): %s", len(drifted), ", ".join(drifted))
    if no_baseline:
        logger.info("No baseline on file for: %s", ", ".join(no_baseline))

    return 1 if drifted else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect configuration drift between stored baselines and live device configs."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Nornir config file (default: config.yaml)",
    )
    parser.add_argument(
        "--hosts",
        help="Comma-separated list of hostnames to target",
    )
    parser.add_argument(
        "--group",
        help="Nornir group name to target",
    )
    parser.add_argument(
        "--baseline-dir",
        default="./baselines",
        help="Directory containing <hostname>.txt baseline files (default: ./baselines)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Overwrite baselines with current config after each comparison (or create if missing)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(run(parse_args()))