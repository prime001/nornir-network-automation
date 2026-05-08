The write was blocked by permissions. Here's the script content:

```python
"""
Config Backup with Change Detection and Rotation

Backs up running configurations from network devices using NAPALM, compares
each backup against the previous version via MD5 checksum, and only writes
a new timestamped archive when the config has actually changed.  A summary
report is printed at the end listing which devices changed, which were
unchanged, and which failed.

Usage:
    python 034_config_backup.py --inventory hosts.yaml --groups-file groups.yaml \
        --backup-dir ./backups --keep 10 [--filter-group core_routers]

Prerequisites:
    pip install nornir nornir-napalm napalm
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml (standard Nornir layout)
"""

import argparse
import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("config_backup")


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _latest_backup(device_dir: Path) -> Path | None:
    backups = sorted(device_dir.glob("*.cfg"))
    return backups[-1] if backups else None


def _rotate(device_dir: Path, keep: int) -> None:
    backups = sorted(device_dir.glob("*.cfg"))
    for old in backups[:-keep]:
        old.unlink()
        log.debug("Rotated out %s", old)


def backup_config(task: Task, backup_dir: Path, keep: int) -> Result:
    device_dir = backup_dir / task.host.name
    device_dir.mkdir(parents=True, exist_ok=True)

    result = task.run(task=napalm_get, getters=["config"])
    running = result[0].result["config"].get("running", "")

    if not running:
        return Result(host=task.host, result="empty", failed=True)

    current_md5 = _md5(running)
    latest = _latest_backup(device_dir)

    if latest:
        prev_md5 = _md5(latest.read_text())
        if current_md5 == prev_md5:
            return Result(host=task.host, result="unchanged")

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = device_dir / f"{ts}.cfg"
    backup_path.write_text(running)
    _rotate(device_dir, keep)

    return Result(host=task.host, result=f"saved:{backup_path}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Nornir config backup with change detection")
    p.add_argument("--inventory", default="hosts.yaml", help="Path to hosts inventory file")
    p.add_argument("--groups-file", default="groups.yaml", help="Path to groups file")
    p.add_argument("--defaults-file", default="defaults.yaml", help="Path to defaults file")
    p.add_argument("--backup-dir", default="./backups", help="Root directory for backups")
    p.add_argument("--keep", type=int, default=10, help="Number of backup revisions to retain per device")
    p.add_argument("--filter-group", help="Only back up hosts in this Nornir group")
    p.add_argument("--workers", type=int, default=10, help="Parallel worker threads")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    for f in (args.inventory, args.groups_file, args.defaults_file):
        if not os.path.exists(f):
            log.error("Required file not found: %s", f)
            sys.exit(1)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.inventory,
                "group_file": args.groups_file,
                "defaults_file": args.defaults_file,
            },
        },
    )

    if args.filter_group:
        nr = nr.filter(F(groups__contains=args.filter_group))

    if not nr.inventory.hosts:
        log.error("No hosts matched the inventory / filter")
        sys.exit(1)

    log.info("Starting backup for %d host(s)", len(nr.inventory.hosts))
    backup_dir = Path(args.backup_dir)

    results = nr.run(task=backup_config, backup_dir=backup_dir, keep=args.keep)

    saved, unchanged, failed = [], [], []
    for host, multi in results.items():
        agg = multi[0]
        if agg.failed:
            failed.append(host)
        elif isinstance(agg.result, str) and agg.result.startswith("saved:"):
            saved.append((host, agg.result.split(":", 1)[1]))
        else:
            unchanged.append(host)

    print("\n=== Backup Summary ===")
    print(f"Changed   ({len(saved)}): {', '.join(h for h, _ in saved) or 'none'}")
    print(f"Unchanged ({len(unchanged)}): {', '.join(unchanged) or 'none'}")
    print(f"Failed    ({len(failed)}): {', '.join(failed) or 'none'}")

    if saved:
        print("\nNew backup paths:")
        for host, path in saved:
            print(f"  {host}: {path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

Key design choices that differentiate this from the existing 004/014/024 backup scripts:

- **MD5 change detection** — skips writing if running config hasn't changed since the last backup, avoiding noise in the archive
- **Timestamped rotation** — backups named `20260429T120000Z.cfg`, kept to `--keep` (default 10) revisions per device
- **Tri-state summary** — explicitly reports changed / unchanged / failed at the end, useful for cron job output
- **NAPALM via `napalm_get`** — pulls structured config, works across IOS/EOS/NXOS/JunOS without per-vendor commands
- **`--filter-group`** — lets you target a subset of inventory (e.g. `core_routers`) without editing the inventory file