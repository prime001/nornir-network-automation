024_config_backup.py — Differential Config Backup with Git Version Tracking

Purpose:
    Retrieves running configurations from network devices via Nornir/NAPALM,
    compares each against the last saved backup to detect drift, and commits
    only changed configs to a local git repository. Produces a per-device
    diff summary and exits non-zero if any device configuration drifted.

Usage:
    python 024_config_backup.py --output-dir ./backups [--group core-routers]
                                 [--commit] [--diff-only] [--workers 5]

Prerequisites:
    pip install nornir nornir-napalm nornir-utils napalm gitpython
    Inventory files: inventory/hosts.yaml, inventory/groups.yaml,
                     inventory/defaults.yaml
    Git repo initialized at --output-dir (or pass --commit to auto-init).
"""

import argparse
import logging
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from git import InvalidGitRepositoryError, Repo
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get
from nornir_utils.plugins.functions import print_result

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("config-backup")


def _backup_path(output_dir: Path, hostname: str) -> Path:
    return output_dir / f"{hostname}.cfg"


def backup_config(task: Task, output_dir: Path, diff_only: bool) -> Result:
    """Nornir task: fetch running config, compare to previous backup."""
    backup_file = _backup_path(output_dir, task.host.name)

    result = task.run(task=napalm_get, getters=["config"])
    running = result[0].result["config"].get("running", "")

    previous = backup_file.read_text() if backup_file.exists() else None
    changed = previous != running

    diff_lines = []
    if previous is not None and changed:
        import difflib
        diff_lines = list(
            difflib.unified_diff(
                previous.splitlines(keepends=True),
                running.splitlines(keepends=True),
                fromfile=f"{task.host.name} (saved)",
                tofile=f"{task.host.name} (current)",
                n=3,
            )
        )

    if not diff_only and changed:
        output_dir.mkdir(parents=True, exist_ok=True)
        backup_file.write_text(running)

    summary = (
        "CHANGED" if changed
        else "UNCHANGED" if previous is not None
        else "NEW"
    )
    diff_text = "".join(diff_lines) if diff_lines else ""
    return Result(
        host=task.host,
        result={"status": summary, "diff": diff_text, "path": str(backup_file)},
    )


def _open_or_init_repo(output_dir: Path) -> Repo:
    try:
        return Repo(output_dir)
    except InvalidGitRepositoryError:
        log.info("Initializing git repo at %s", output_dir)
        repo = Repo.init(output_dir)
        gitignore = output_dir / ".gitignore"
        gitignore.write_text("*.tmp\n")
        repo.index.add([".gitignore"])
        repo.index.commit("chore: init config backup repository")
        return repo


def _commit_changes(repo: Repo, changed_hosts: list[str]) -> str:
    repo.index.add([f"{h}.cfg" for h in changed_hosts])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"backup: config drift detected on {len(changed_hosts)} device(s) [{ts}]\n\n"
    msg += "\n".join(f"  - {h}" for h in changed_hosts)
    commit = repo.index.commit(msg)
    return commit.hexsha


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Differential network config backup with git tracking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Backup all devices, commit changes:
              python 024_config_backup.py --output-dir ./backups --commit

              # Only report drift, write no files:
              python 024_config_backup.py --diff-only

              # Scope to a Nornir group:
              python 024_config_backup.py --group core-routers --commit
        """),
    )
    parser.add_argument(
        "--output-dir", default="./backups",
        help="Directory to store config files (default: ./backups)"
    )
    parser.add_argument(
        "--group", default=None,
        help="Filter to a Nornir host group (e.g. 'core-routers')"
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Commit changed configs to git after backup"
    )
    parser.add_argument(
        "--diff-only", action="store_true",
        help="Detect drift but do not write or commit files"
    )
    parser.add_argument(
        "--workers", type=int, default=5,
        help="Nornir parallel workers (default: 5)"
    )
    parser.add_argument(
        "--inventory", default="inventory",
        help="Path to Nornir inventory directory (default: inventory)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    output_dir = Path(args.output_dir).resolve()

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

    if args.group:
        nr = nr.filter(F(groups__contains=args.group))
        log.info("Scoped to group '%s': %d host(s)", args.group, len(nr.inventory.hosts))

    if not nr.inventory.hosts:
        log.error("No hosts matched — check inventory or --group filter.")
        return 1

    log.info("Starting config backup for %d host(s)", len(nr.inventory.hosts))
    results = nr.run(
        task=backup_config,
        name="differential-config-backup",
        output_dir=output_dir,
        diff_only=args.diff_only,
    )
    print_result(results)

    changed, new, failed = [], [], []
    for host, multi in results.items():
        if multi.failed:
            failed.append(host)
            continue
        status = multi[1].result["status"]
        if status == "CHANGED":
            changed.append(host)
            diff = multi[1].result["diff"]
            if diff:
                log.info("Diff for %s:\n%s", host, diff)
        elif status == "NEW":
            new.append(host)

    log.info(
        "Summary — changed: %d, new: %d, unchanged: %d, failed: %d",
        len(changed), len(new),
        len(nr.inventory.hosts) - len(changed) - len(new) - len(failed),
        len(failed),
    )

    drifted = changed + new
    if drifted and args.commit and not args.diff_only:
        repo = _open_or_init_repo(output_dir)
        sha = _commit_changes(repo, drifted)
        log.info("Committed %d config(s) → %s", len(drifted), sha)

    if failed:
        log.warning("Failed hosts: %s", ", ".join(failed))
        return 2

    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())