The instruction says "Output ONLY the script content" — here it is:

```
"""
unsaved_changes.py - Detect devices with unsaved running-config changes.

Purpose:
    Compares running-config against startup-config across the fleet to identify
    devices where changes have been made but not written to NVRAM. Optionally
    saves configs on affected devices via 'write memory'.

Usage:
    python 005_unsaved_changes.py [--hosts HOST [HOST ...]]
                                   [--groups GROUP [GROUP ...]]
                                   [--save] [--workers N]
                                   [--inventory INVENTORY_DIR]

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils

    Inventory files (hosts.yaml, groups.yaml, defaults.yaml) must exist in the
    directory specified by --inventory (default: ./inventory).

Examples:
    python 005_unsaved_changes.py
    python 005_unsaved_changes.py --groups core-routers --save
    python 005_unsaved_changes.py --hosts rtr1 rtr2 --workers 20
"""

import argparse
import difflib
import logging
import sys
from typing import Optional

from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.filter import F
from nornir.core.task import MultiResult, Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("unsaved_changes")


def _normalize_config(raw: str) -> list[str]:
    """Strip timestamps, nonces, and blank lines that cause false-positive diffs."""
    skip_prefixes = (
        "! Last configuration change",
        "! NVRAM config last updated",
        "Building configuration",
        "Current configuration",
        "ntp clock-period",
    )
    lines = []
    for line in raw.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped == "!":
            continue
        if any(stripped.startswith(p) for p in skip_prefixes):
            continue
        lines.append(stripped + "\n")
    return lines


def check_unsaved_changes(task: Task, save: bool = False) -> Result:
    """Fetch running and startup configs, diff them, optionally save."""
    running_result = task.run(
        task=netmiko_send_command,
        command_string="show running-config",
        name="show running-config",
    )
    startup_result = task.run(
        task=netmiko_send_command,
        command_string="show startup-config",
        name="show startup-config",
    )

    running = _normalize_config(running_result.result)
    startup = _normalize_config(startup_result.result)

    diff_lines = list(
        difflib.unified_diff(
            startup,
            running,
            fromfile="startup-config",
            tofile="running-config",
            lineterm="",
        )
    )

    has_changes = bool(diff_lines)
    diff_text = "".join(diff_lines) if has_changes else ""

    saved = False
    if has_changes and save:
        task.run(
            task=netmiko_send_command,
            command_string="write memory",
            name="write memory",
        )
        saved = True
        log.info("%s: config saved", task.host.name)

    return Result(
        host=task.host,
        result={
            "has_changes": has_changes,
            "diff": diff_text,
            "saved": saved,
            "changed_lines": len(
                [l for l in diff_lines if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
            ),
        },
    )


def build_nornir(inventory_dir: str, workers: int) -> Nornir:
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": f"{inventory_dir}/hosts.yaml",
                "group_file": f"{inventory_dir}/groups.yaml",
                "defaults_file": f"{inventory_dir}/defaults.yaml",
            },
        },
        logging={"enabled": False},
    )


def filter_nornir(
    nr: Nornir,
    hosts: Optional[list[str]],
    groups: Optional[list[str]],
) -> Nornir:
    if hosts:
        nr = nr.filter(F(name__any=hosts))
    if groups:
        nr = nr.filter(F(groups__any=groups))
    return nr


def print_summary(results: MultiResult, save: bool) -> int:
    """Print a concise per-device summary; return count of devices with changes."""
    devices_with_changes = 0
    devices_failed = 0

    print("\n" + "=" * 64)
    print(f"{'HOST':<30} {'STATUS':<15} {'CHANGED LINES':>13}")
    print("=" * 64)

    for host, multi in results.items():
        if multi.failed:
            devices_failed += 1
            print(f"{host:<30} {'ERROR':<15} {'N/A':>13}")
            continue

        data = multi[0].result
        if data["has_changes"]:
            devices_with_changes += 1
            status = "SAVED" if data["saved"] else "UNSAVED"
            print(f"{host:<30} {status:<15} {data['changed_lines']:>13}")
        else:
            print(f"{host:<30} {'IN SYNC':<15} {'0':>13}")

    print("=" * 64)
    total = len(results)
    print(
        f"Checked {total} device(s): "
        f"{devices_with_changes} unsaved, "
        f"{total - devices_with_changes - devices_failed} in sync, "
        f"{devices_failed} failed"
    )

    if devices_with_changes and not save:
        print("\nTip: re-run with --save to write memory on affected devices.")

    return devices_with_changes


def print_diffs(results: MultiResult) -> None:
    """Print full unified diffs for devices that have changes."""
    for host, multi in results.items():
        if multi.failed or not multi[0].result.get("has_changes"):
            continue
        print(f"\n{'─' * 64}")
        print(f"DIFF: {host}")
        print(f"{'─' * 64}")
        print(multi[0].result["diff"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect devices with unsaved running-config changes."
    )
    parser.add_argument("--hosts", nargs="+", metavar="HOST", help="Target specific hosts")
    parser.add_argument("--groups", nargs="+", metavar="GROUP", help="Target specific groups")
    parser.add_argument(
        "--save", action="store_true", help="Write memory on devices with unsaved changes"
    )
    parser.add_argument(
        "--workers", type=int, default=10, metavar="N",
        help="Parallel worker threads (default: 10)"
    )
    parser.add_argument(
        "--inventory", default="inventory", metavar="DIR",
        help="Inventory directory (default: ./inventory)"
    )
    parser.add_argument(
        "--diff", action="store_true", help="Print unified diff for each device with changes"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    try:
        nr = build_nornir(args.inventory, args.workers)
    except Exception as exc:
        log.error("Failed to initialize Nornir: %s", exc)
        sys.exit(1)

    nr = filter_nornir(nr, args.hosts, args.groups)

    if not nr.inventory.hosts:
        print("No hosts matched the given filters.", file=sys.stderr)
        sys.exit(1)

    print(f"Checking {len(nr.inventory.hosts)} device(s) for unsaved changes...")

    results = nr.run(
        task=check_unsaved_changes,
        save=args.save,
        name="unsaved_changes",
    )

    if args.diff:
        print_diffs(results)

    unsaved_count = print_summary(results, args.save)
    sys.exit(1 if unsaved_count else 0)
```