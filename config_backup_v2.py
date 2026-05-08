config_diff.py — Nornir configuration change detection and unified diff reporter.

Purpose:
    Connects to network devices, retrieves their running configurations, and
    compares them against previously saved baselines. Reports a unified diff
    for every device where the configuration has changed since the last run.
    Useful for detecting unauthorized changes, auditing drift, or validating
    that a change window actually modified what was intended.

Usage:
    python config_diff.py --inventory config.yaml --baseline-dir ./baselines
    python config_diff.py --inventory config.yaml --baseline-dir ./baselines --save
    python config_diff.py --inventory config.yaml --filter site=dc1 --report-file changes.txt

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    A nornir inventory (config.yaml referencing hosts.yaml / groups.yaml /
    defaults.yaml) targeting devices that respond to 'show running-config'.
    Run once with --save to establish the initial baseline.
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


def load_baseline(host_name: str, baseline_dir: Path) -> str | None:
    path = baseline_dir / f"{host_name}.cfg"
    return path.read_text(encoding="utf-8") if path.exists() else None


def save_baseline(host_name: str, config: str, baseline_dir: Path) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    path = baseline_dir / f"{host_name}.cfg"
    path.write_text(config, encoding="utf-8")
    logger.info("Saved baseline: %s", path)


def compute_diff(old: str, new: str, host_name: str) -> list[str]:
    return list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{host_name}/baseline",
            tofile=f"{host_name}/current",
        )
    )


def run_diff(args: argparse.Namespace) -> int:
    baseline_dir = Path(args.baseline_dir)
    diff_sections: list[str] = []
    changed: list[str] = []
    unchanged: list[str] = []
    new_devices: list[str] = []
    errors: list[str] = []

    nr = InitNornir(config_file=args.inventory)

    if args.filter:
        key, _, value = args.filter.partition("=")
        nr = nr.filter(F(**{key: value}))

    logger.info("Connecting to %d device(s)...", len(nr.inventory.hosts))
    results = nr.run(task=fetch_running_config)

    for host_name, multi_result in results.items():
        if multi_result.failed:
            logger.error("Failed to retrieve config from %s", host_name)
            errors.append(host_name)
            continue

        current_config: str = multi_result[0].result or ""
        if not current_config.strip():
            logger.warning("Empty config returned from %s", host_name)
            errors.append(host_name)
            continue

        baseline = load_baseline(host_name, baseline_dir)

        if baseline is None:
            new_devices.append(host_name)
            logger.info("No baseline for %s — treating as new device", host_name)
            if args.save:
                save_baseline(host_name, current_config, baseline_dir)
            continue

        diff = compute_diff(baseline, current_config, host_name)

        if diff:
            changed.append(host_name)
            diff_sections.append(f"\n{'=' * 60}")
            diff_sections.append(f"CHANGED: {host_name}")
            diff_sections.append(f"{'=' * 60}")
            diff_sections.extend(diff)
            if args.save:
                save_baseline(host_name, current_config, baseline_dir)
        else:
            unchanged.append(host_name)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary_lines = [
        f"\n{'=' * 60}",
        f"Config Diff Report — {timestamp}",
        f"{'=' * 60}",
        f"  Changed  : {len(changed):>3}  {', '.join(changed) or '—'}",
        f"  Unchanged: {len(unchanged):>3}",
        f"  New      : {len(new_devices):>3}  {', '.join(new_devices) or '—'}",
        f"  Errors   : {len(errors):>3}  {', '.join(errors) or '—'}",
    ]

    if diff_sections:
        print("".join(diff_sections))
    print("\n".join(summary_lines))

    if args.report_file:
        report_path = Path(args.report_file)
        report_path.write_text(
            "\n".join(summary_lines) + "\n" + "".join(diff_sections),
            encoding="utf-8",
        )
        logger.info("Report written to %s", report_path)

    return 1 if (changed or errors) else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect and report running-config drift across network devices."
    )
    parser.add_argument(
        "--inventory",
        default="config.yaml",
        help="Nornir config file (default: config.yaml)",
    )
    parser.add_argument(
        "--baseline-dir",
        default="./baselines",
        help="Directory of baseline .cfg files (default: ./baselines)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Update baselines with current configs after comparing",
    )
    parser.add_argument(
        "--filter",
        metavar="KEY=VALUE",
        help="Filter inventory by host attribute, e.g. site=dc1",
    )
    parser.add_argument(
        "--report-file",
        metavar="PATH",
        help="Write the full diff report to a file",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    sys.exit(run_diff(args))


if __name__ == "__main__":
    main()