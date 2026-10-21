The user asked for output-only, so I'll just print the script directly:

```
"""
config_change_detector.py — Detect configuration drift across network devices.
```

Wait, the instructions say "Output ONLY the script content, no markdown fences, no explanation." — meaning the entire response should just be the raw Python. Let me do that:

---

"""
config_change_detector.py — Detect configuration drift across network devices.

Purpose:
    Pulls running configurations from devices via Nornir/Netmiko, compares each
    against a stored baseline, and reports unified diffs. Useful for unauthorized-
    change auditing, pre/post maintenance validation, and scheduled drift checks.

Usage:
    # First run: save baselines
    python config_change_detector.py --save-baseline

    # Subsequent runs: detect changes
    python config_change_detector.py

    # Target a specific group and update baselines when changes are found
    python config_change_detector.py --group core-routers --update-on-change

    # Single device
    python config_change_detector.py --host spine-01 --save-baseline

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml (standard Nornir layout)
    Baseline directory (default: ./baselines) is created automatically.
"""

import argparse
import difflib
import hashlib
import logging
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("config_change_detector")


def get_running_config(task: Task) -> Result:
    """Retrieve running configuration from a device."""
    platform = task.host.platform or "cisco_ios"
    command_map = {
        "cisco_ios": "show running-config",
        "cisco_nxos": "show running-config",
        "juniper_junos": "show configuration | display set",
        "arista_eos": "show running-config",
    }
    command = command_map.get(platform, "show running-config")
    result = task.run(task=netmiko_send_command, command_string=command)
    return Result(host=task.host, result=result.result)


def detect_changes(task: Task, baseline_dir: Path, update_on_change: bool) -> Result:
    """Compare current config against baseline; return diff if changed."""
    config_result = task.run(task=get_running_config)
    current = config_result.result.strip()

    baseline_path = baseline_dir / f"{task.host.name}.cfg"

    if not baseline_path.exists():
        return Result(
            host=task.host,
            result="NO BASELINE — run with --save-baseline to create one.",
            failed=False,
        )

    baseline = baseline_path.read_text().strip()
    current_hash = hashlib.sha256(current.encode()).hexdigest()
    baseline_hash = hashlib.sha256(baseline.encode()).hexdigest()

    if current_hash == baseline_hash:
        return Result(host=task.host, result="NO CHANGE", failed=False)

    diff_lines = list(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"{task.host.name} (baseline)",
            tofile=f"{task.host.name} (current)",
        )
    )
    diff_text = "".join(diff_lines)

    if update_on_change:
        baseline_path.write_text(current)
        log.info("Baseline updated for %s", task.host.name)

    return Result(host=task.host, result=f"CHANGED\n{diff_text}", failed=False)


def save_baseline(task: Task, baseline_dir: Path) -> Result:
    """Save current running config as the baseline for this host."""
    config_result = task.run(task=get_running_config)
    config = config_result.result.strip()
    baseline_path = baseline_dir / f"{task.host.name}.cfg"
    baseline_path.write_text(config)
    return Result(
        host=task.host,
        result=f"Baseline saved to {baseline_path}",
        failed=False,
    )


def build_report(results: dict) -> dict:
    """Summarise per-device outcomes into a report dict."""
    summary = {"changed": [], "unchanged": [], "no_baseline": [], "failed": []}
    for host, mr in results.items():
        if mr.failed:
            summary["failed"].append(host)
        else:
            msg = mr[0].result if mr else ""
            if "CHANGED" in msg:
                summary["changed"].append(host)
            elif "NO BASELINE" in msg:
                summary["no_baseline"].append(host)
            else:
                summary["unchanged"].append(host)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect config drift across network devices using Nornir."
    )
    parser.add_argument("--hosts", default="hosts.yaml", help="Nornir hosts file")
    parser.add_argument("--groups", default="groups.yaml", help="Nornir groups file")
    parser.add_argument("--defaults", default="defaults.yaml", help="Nornir defaults file")
    parser.add_argument(
        "--baseline-dir", default="./baselines", help="Baseline storage directory"
    )
    parser.add_argument(
        "--group", help="Filter: only check hosts in this Nornir group"
    )
    parser.add_argument("--host", help="Filter: only check this single host")
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save current configs as new baselines (overwrites existing)",
    )
    parser.add_argument(
        "--update-on-change",
        action="store_true",
        help="Update the baseline whenever a change is detected",
    )
    parser.add_argument(
        "--workers", type=int, default=10, help="Concurrent workers (default: 10)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show full per-device Nornir output"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline_dir = Path(args.baseline_dir)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    try:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
            inventory={
                "plugin": "SimpleInventory",
                "options": {
                    "host_file": args.hosts,
                    "group_file": args.groups,
                    "defaults_file": args.defaults,
                },
            },
        )
    except Exception as exc:
        log.error("Failed to initialize Nornir: %s", exc)
        return 1

    if args.host:
        nr = nr.filter(F(name=args.host))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))

    if not nr.inventory.hosts:
        log.error("No hosts matched the given filters.")
        return 1

    log.info(
        "Running against %d host(s) — mode: %s",
        len(nr.inventory.hosts),
        "save-baseline" if args.save_baseline else "detect-changes",
    )

    if args.save_baseline:
        results = nr.run(task=save_baseline, baseline_dir=baseline_dir)
    else:
        results = nr.run(
            task=detect_changes,
            baseline_dir=baseline_dir,
            update_on_change=args.update_on_change,
        )

    if args.verbose:
        print_result(results)

    if not args.save_baseline:
        report = build_report(results)
        print(f"\n{'='*60}")
        print(f"Config Drift Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        print(f"  Changed:     {len(report['changed'])}  {report['changed']}")
        print(f"  Unchanged:   {len(report['unchanged'])}")
        print(f"  No baseline: {len(report['no_baseline'])}  {report['no_baseline']}")
        print(f"  Failed:      {len(report['failed'])}  {report['failed']}")
        print(f"{'='*60}\n")
        return 1 if report["changed"] or report["failed"] else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())