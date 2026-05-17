```python
"""config_drift.py - Detect configuration drift between running and startup configs.

Purpose:
    Connects to network devices via Nornir/Netmiko and compares each device's
    running-config against its startup-config. Devices with unsaved changes are
    flagged so engineers can identify what will be lost on the next reboot.
    Outputs a per-device unified diff and an optional summary report file.
    Exits non-zero when drift is found, making it usable in scheduled CI checks.

Usage:
    python config_drift.py --hosts router1,router2 --username admin --password secret
    python config_drift.py --group core --username admin --save-report drift.txt
    python config_drift.py --group all --username admin --fail-on-drift

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Inventory files: inventory/hosts.yaml, inventory/groups.yaml, inventory/defaults.yaml
"""

import argparse
import difflib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _fetch_config(task: Task, command: str) -> str:
    result = task.run(
        task=netmiko_send_command,
        command_string=command,
        use_textfsm=False,
        name=command,
    )
    return result.result


def check_drift(task: Task) -> Result:
    running = _fetch_config(task, "show running-config")
    startup = _fetch_config(task, "show startup-config")

    diff = list(
        difflib.unified_diff(
            startup.splitlines(keepends=True),
            running.splitlines(keepends=True),
            fromfile=f"{task.host.name}:startup-config",
            tofile=f"{task.host.name}:running-config",
            lineterm="",
        )
    )
    return Result(
        host=task.host,
        result={"has_drift": bool(diff), "diff": diff},
    )


def build_nornir(args: argparse.Namespace):
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.host_file,
                "group_file": args.group_file,
                "defaults_file": args.defaults_file,
            },
        },
        logging={"enabled": False},
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if args.hosts:
        targets = [h.strip() for h in args.hosts.split(",")]
        nr = nr.filter(F(name__any=targets))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))

    return nr


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect unsaved config changes (running vs startup) across network devices."
    )
    parser.add_argument("--hosts", help="Comma-separated hostnames to target")
    parser.add_argument("--group", help="Inventory group to target")
    parser.add_argument("--username", help="SSH username (overrides inventory defaults)")
    parser.add_argument("--password", help="SSH password (overrides inventory defaults)")
    parser.add_argument(
        "--workers", type=int, default=10, help="Parallel workers (default: 10)"
    )
    parser.add_argument("--host-file", default="inventory/hosts.yaml")
    parser.add_argument("--group-file", default="inventory/groups.yaml")
    parser.add_argument("--defaults-file", default="inventory/defaults.yaml")
    parser.add_argument(
        "--save-report", metavar="FILE", help="Write diff report to this file"
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit with code 1 if any device has unsaved changes (CI mode)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    nr = build_nornir(args)

    if not nr.inventory.hosts:
        logger.error("No hosts matched the given filter. Check --hosts / --group.")
        return 2

    total = len(nr.inventory.hosts)
    logger.info("Checking config drift on %d device(s)...", total)
    results = nr.run(task=check_drift, name="config-drift-check")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report_lines = [f"Config Drift Report — {timestamp}\n", "=" * 60 + "\n"]
    drifted = []
    errors = []

    for hostname, multi_result in results.items():
        if multi_result.failed:
            exc = multi_result[0].exception
            logger.error("%s: connection/task failed — %s", hostname, exc)
            errors.append(hostname)
            report_lines.append(f"\n[{hostname}] — ERROR: {exc}\n")
            continue

        data = multi_result[0].result
        if data["has_drift"]:
            drifted.append(hostname)
            diff_text = "\n".join(data["diff"])
            logger.warning(
                "%s: DRIFT DETECTED — %d changed lines", hostname, len(data["diff"])
            )
            report_lines.append(f"\n[{hostname}] — DRIFT DETECTED\n")
            report_lines.append(diff_text + "\n")
        else:
            logger.info("%s: clean — running matches startup", hostname)
            report_lines.append(f"\n[{hostname}] — CLEAN\n")

    report_lines.append("\n" + "=" * 60 + "\n")
    report_lines.append(
        f"Summary: {len(drifted)} drifted / {len(errors)} errors / {total} total\n"
    )
    if drifted:
        report_lines.append("Drifted devices: " + ", ".join(drifted) + "\n")
    if errors:
        report_lines.append("Errored devices: " + ", ".join(errors) + "\n")

    report_text = "".join(report_lines)
    print(report_text)

    if args.save_report:
        Path(args.save_report).write_text(report_text)
        logger.info("Report saved to %s", args.save_report)

    if args.fail_on_drift and drifted:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```