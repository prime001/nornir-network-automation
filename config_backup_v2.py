config_drift.py — Detect configuration drift against saved backup snapshots.

Purpose:
    Compares each device's current running configuration against the most
    recently saved backup file on disk. Devices with no saved backup are
    flagged as untracked. Useful for post-maintenance audits and detecting
    unauthorized or undocumented changes across the fleet.

Usage:
    python config_drift.py --backup-dir ./backups
    python config_drift.py --backup-dir ./backups --group core-routers
    python config_drift.py --backup-dir ./backups --host sw-01 --save

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml in --inventory dir
    Backup files named <hostname>.txt must exist in --backup-dir for comparison;
    run once with --save to create the initial baseline for each device.

Exit codes:
    0 — all devices clean (no drift)
    1 — fatal error (inventory empty, connection failures for all hosts)
    2 — drift detected on one or more devices
"""

import argparse
import difflib
import logging
import sys
from pathlib import Path

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

MAX_DIFF_LINES = 80


def compare_config(task: Task, backup_dir: Path, save: bool) -> Result:
    """Pull running config and diff it against the saved backup for this host."""
    backup_file = backup_dir / f"{task.host.name}.txt"

    fetch = task.run(
        task=netmiko_send_command,
        command_string="show running-config",
        use_textfsm=False,
    )
    running_lines = fetch[0].result.strip().splitlines(keepends=True)

    if not backup_file.exists():
        if save:
            backup_file.write_text("".join(running_lines))
            return Result(
                host=task.host,
                result=f"[BASELINE] {task.host.name}: no prior backup — baseline saved.",
                changed=True,
            )
        return Result(
            host=task.host,
            result=f"[UNTRACKED] {task.host.name}: no backup found at {backup_file}",
            changed=False,
        )

    saved_lines = backup_file.read_text().strip().splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            saved_lines,
            running_lines,
            fromfile=f"{task.host.name} (saved)",
            tofile=f"{task.host.name} (running)",
            lineterm="",
        )
    )

    if not diff:
        return Result(
            host=task.host,
            result=f"[CLEAN] {task.host.name}: running config matches backup",
            changed=False,
        )

    shown = diff[:MAX_DIFF_LINES]
    truncated = len(diff) - MAX_DIFF_LINES
    body = "".join(shown)
    if truncated > 0:
        body += f"\n... ({truncated} more diff lines not shown)"

    summary = f"[DRIFT] {task.host.name}: {len(diff)} diff lines\n{body}"

    if save:
        backup_file.write_text("".join(running_lines))
        summary += "\n[SAVED] Backup updated with current running config."

    return Result(host=task.host, result=summary, changed=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect per-device config drift against saved backup snapshots.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--backup-dir",
        default="./backups",
        metavar="DIR",
        help="Directory containing per-device backup .txt files",
    )
    p.add_argument(
        "--inventory",
        default=".",
        metavar="DIR",
        help="Directory containing hosts.yaml, groups.yaml, defaults.yaml",
    )
    p.add_argument("--host", metavar="NAME", help="Target a single host by inventory name")
    p.add_argument("--group", metavar="NAME", help="Target a device group from inventory")
    p.add_argument(
        "--save",
        action="store_true",
        help="After diff, write current running config as the new backup baseline",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=10,
        metavar="N",
        help="Concurrent worker threads",
    )
    p.add_argument("--username", metavar="USER", help="Override inventory username")
    p.add_argument("--password", metavar="PASS", help="Override inventory password")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": f"{args.inventory}/hosts.yaml",
                "group_file": f"{args.inventory}/groups.yaml",
                "defaults_file": f"{args.inventory}/defaults.yaml",
            },
        },
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if args.host:
        nr = nr.filter(name=args.host)
    elif args.group:
        nr = nr.filter(groups__contains=args.group)

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check --host / --group or inventory files.")
        sys.exit(1)

    logger.info(
        "Checking %d device(s) for drift; backups in: %s",
        len(nr.inventory.hosts),
        backup_dir,
    )

    results = nr.run(
        task=compare_config,
        backup_dir=backup_dir,
        save=args.save,
        name="config_drift",
    )

    failed = [h for h, r in results.items() if r.failed]
    drifted = [h for h, r in results.items() if not r.failed and r.changed]
    clean = [h for h, r in results.items() if not r.failed and not r.changed]

    print("\n" + "=" * 64)
    for host_name, host_result in results.items():
        if host_result.failed:
            print(f"[ERROR] {host_name}: {host_result.exception}")
        else:
            print(host_result.result)
    print("=" * 64)
    print(
        f"Summary: {len(clean)} clean  |  {len(drifted)} drifted  |  {len(failed)} failed"
        f"  |  {len(nr.inventory.hosts)} total"
    )

    if failed and not drifted and not clean:
        sys.exit(1)
    if drifted:
        sys.exit(2)


if __name__ == "__main__":
    main()