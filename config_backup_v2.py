Writing a config drift detection script — distinct from the existing backup scripts, practical for real-world change auditing.

```python
"""
config_diff.py — Configuration Drift Detection for Network Devices

Purpose:
    Retrieve running configurations from network devices via Nornir and compare
    them against previously saved baseline snapshots. Produces unified diffs that
    highlight configuration changes between audit intervals, enabling drift
    detection and unauthorized-change alerting.

Usage:
    # First run: establish baselines
    python config_diff.py --save-baseline --baseline-dir ./baselines

    # Subsequent runs: detect drift
    python config_diff.py --baseline-dir ./baselines --output-dir ./diffs

    # Target specific hosts or groups
    python config_diff.py --hosts router-01 router-02 --baseline-dir ./baselines
    python config_diff.py --groups core-routers --baseline-dir ./baselines

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    hosts.yaml and groups.yaml in the working directory (or set NORNIR_CONFIG)
"""

import argparse
import difflib
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)

SHOW_RUN = {
    "cisco_ios": "show running-config",
    "cisco_ios_xe": "show running-config",
    "cisco_nxos": "show running-config",
    "cisco_xr": "show running-config",
    "arista_eos": "show running-config",
    "juniper_junos": "show configuration",
    "linux": "cat /etc/network/interfaces",
}
DEFAULT_COMMAND = "show running-config"


def retrieve_config(task: Task) -> Result:
    platform = task.host.platform or "cisco_ios"
    command = SHOW_RUN.get(platform, DEFAULT_COMMAND)
    result = task.run(
        task=netmiko_send_command,
        command_string=command,
        use_textfsm=False,
    )
    return Result(host=task.host, result=result.result)


def load_baseline(host: str, baseline_dir: Path) -> Optional[str]:
    path = baseline_dir / f"{host}.cfg"
    if path.exists():
        return path.read_text()
    logger.warning("No baseline found for %s at %s", host, path)
    return None


def save_config(host: str, config: str, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{host}.cfg"
    path.write_text(config)
    logger.info("Baseline saved: %s", path)


def unified_diff(baseline: str, current: str, host: str) -> str:
    diff = difflib.unified_diff(
        baseline.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=f"{host}/baseline",
        tofile=f"{host}/current",
        lineterm="",
    )
    return "\n".join(diff)


def write_diff_report(host: str, diff: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{host}_{ts}.diff"
    path.write_text(diff)
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Detect configuration drift against saved baselines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--hosts", nargs="+", metavar="HOST", help="Filter by hostname")
    p.add_argument("--groups", nargs="+", metavar="GROUP", help="Filter by inventory group")
    p.add_argument("--baseline-dir", default="baselines", metavar="DIR",
                   help="Directory for baseline .cfg files (default: baselines/)")
    p.add_argument("--output-dir", default="diffs", metavar="DIR",
                   help="Directory for diff reports (default: diffs/)")
    p.add_argument("--save-baseline", action="store_true",
                   help="Save current configs as new baselines instead of diffing")
    p.add_argument("--username", metavar="USER", help="Override inventory username")
    p.add_argument("--password", metavar="PASS", help="Override inventory password")
    p.add_argument("--platform", metavar="PLATFORM",
                   help="Override platform for all hosts (e.g. cisco_ios)")
    p.add_argument("--workers", type=int, default=10, metavar="N",
                   help="Parallel worker threads (default: 10)")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    return p


def main() -> int:
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        logging={"enabled": False},
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password
    if args.platform:
        nr.inventory.defaults.platform = args.platform

    target = nr
    if args.hosts:
        target = target.filter(F(name__any=args.hosts))
    if args.groups:
        target = target.filter(F(groups__any=args.groups))

    if not target.inventory.hosts:
        logger.error("No hosts matched — check --hosts / --groups filters")
        return 2

    logger.info("Querying %d host(s)...", len(target.inventory.hosts))
    results = target.run(task=retrieve_config, name="retrieve_config")

    baseline_dir = Path(args.baseline_dir)
    output_dir = Path(args.output_dir)
    changed, clean, errors = [], [], []

    for host, multi_result in results.items():
        if multi_result.failed:
            exc = multi_result[0].exception
            logger.error("Failed on %s: %s", host, exc)
            errors.append(host)
            continue

        config = multi_result[0].result

        if args.save_baseline:
            save_config(host, config, baseline_dir)
            continue

        baseline = load_baseline(host, baseline_dir)
        if baseline is None:
            errors.append(host)
            continue

        diff = unified_diff(baseline, config, host)
        if diff:
            report_path = write_diff_report(host, diff, output_dir)
            logger.warning("DRIFT on %-30s → %s", host, report_path)
            print(f"\n{'='*64}\nDrift detected: {host}\n{'='*64}\n{diff}")
            changed.append(host)
        else:
            logger.info("Clean: %s", host)
            clean.append(host)

    if args.save_baseline:
        logger.info("Done. Baselines saved to %s/", baseline_dir)
        return 0

    print(f"\n{'='*64}")
    print(f"Summary: {len(changed)} drifted  |  {len(clean)} clean  |  {len(errors)} errors")
    if changed:
        print(f"Changed : {', '.join(changed)}")
    if errors:
        print(f"Errors  : {', '.join(errors)}")
    print(f"{'='*64}")

    return 1 if (changed or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
```