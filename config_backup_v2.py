config_drift.py - Configuration Drift Detector

Compares current running configurations against saved baseline backups to
detect unauthorized or unexpected changes. Outputs a unified diff per device
and exits non-zero if any drift is found, making it suitable for CI/CD
pipeline integration or scheduled monitoring.

Usage:
    python config_drift.py --backup-dir ./backups --username admin --password secret
    python config_drift.py --backup-dir ./backups --host 192.168.1.1 --username admin
    python config_drift.py --backup-dir ./backups --save-on-drift --quiet

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Baseline backups must exist under --backup-dir as <hostname>.cfg files.
    Inventory configured via nornir config file or --host for ad-hoc runs.
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
from nornir_utils.plugins.functions import print_title

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("config_drift")


def fetch_running_config(task: Task) -> Result:
    result = task.run(
        task=netmiko_send_command,
        command_string="show running-config",
        use_textfsm=False,
    )
    return Result(host=task.host, result=result.result)


def load_baseline(backup_dir: Path, hostname: str) -> str | None:
    candidates = [
        backup_dir / f"{hostname}.cfg",
        backup_dir / f"{hostname}.txt",
        backup_dir / f"{hostname}.conf",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(errors="replace")
    return None


def diff_configs(baseline: str, current: str, hostname: str) -> list[str]:
    baseline_lines = baseline.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)
    return list(
        difflib.unified_diff(
            baseline_lines,
            current_lines,
            fromfile=f"{hostname} (baseline)",
            tofile=f"{hostname} (current)",
            lineterm="",
        )
    )


def save_config(backup_dir: Path, hostname: str, config: str) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{hostname}.cfg"
    path.write_text(config)
    log.info("Saved updated baseline for %s to %s", hostname, path)


def check_drift(task: Task, backup_dir: Path, save_on_drift: bool) -> Result:
    hostname = task.host.name
    baseline = load_baseline(backup_dir, hostname)
    if baseline is None:
        return Result(
            host=task.host,
            result=None,
            failed=False,
            severity_level=logging.WARNING,
        )

    fetch_result = task.run(task=fetch_running_config)
    current = fetch_result[0].result
    if not current:
        return Result(host=task.host, result=None, failed=True)

    diff = diff_configs(baseline, current, hostname)

    if diff and save_on_drift:
        save_config(backup_dir, hostname, current)

    return Result(host=task.host, result={"diff": diff, "current": current})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect configuration drift against saved baselines."
    )
    parser.add_argument(
        "--backup-dir",
        required=True,
        metavar="DIR",
        help="Directory containing baseline .cfg files named <hostname>.cfg",
    )
    parser.add_argument("--config", default="nornir.yaml", help="Nornir config file")
    parser.add_argument("--host", help="Filter to a single hostname or IP")
    parser.add_argument("--group", help="Filter to a device group")
    parser.add_argument("--username", help="Override username from inventory")
    parser.add_argument("--password", help="Override password from inventory")
    parser.add_argument(
        "--save-on-drift",
        action="store_true",
        help="Overwrite baseline with current config when drift is detected",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress diff output; exit code still reflects drift",
    )
    parser.add_argument("--workers", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backup_dir = Path(args.backup_dir)

    nr = InitNornir(config_file=args.config, core={"num_workers": args.workers})

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password
    if args.host:
        nr = nr.filter(F(name=args.host) | F(hostname=args.host))
    if args.group:
        nr = nr.filter(F(groups__contains=args.group))

    if not nr.inventory.hosts:
        log.error("No hosts matched the given filters.")
        return 2

    print_title(f"Config Drift Check — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    results = nr.run(
        task=check_drift,
        backup_dir=backup_dir,
        save_on_drift=args.save_on_drift,
    )

    drifted: list[str] = []
    no_baseline: list[str] = []

    for hostname, multi in results.items():
        if multi.failed:
            print(f"[ERROR]   {hostname}: failed to retrieve config")
            continue

        data = multi[0].result
        if data is None:
            no_baseline.append(hostname)
            print(f"[SKIP]    {hostname}: no baseline found in {backup_dir}")
            continue

        diff = data["diff"]
        if diff:
            drifted.append(hostname)
            print(f"[DRIFT]   {hostname}: {len(diff)} changed lines")
            if not args.quiet:
                print("\n".join(diff))
                print()
        else:
            print(f"[OK]      {hostname}: no drift detected")

    print(
        f"\nSummary: {len(drifted)} drifted, "
        f"{len(no_baseline)} skipped (no baseline), "
        f"{len(results) - len(drifted) - len(no_baseline)} clean"
    )

    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())