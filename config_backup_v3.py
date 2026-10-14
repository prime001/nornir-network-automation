Writing a differential config backup script with change detection — different from the existing plain backup scripts by tracking hashes and generating diffs only when configs change.

```python
"""
config_backup_diff.py — Differential Configuration Backup with Change Detection

Purpose:
    Backs up device running configurations using Nornir/Netmiko, but only writes
    new backup files when the configuration has actually changed. Tracks config
    hashes across runs and generates unified diffs to show exactly what changed.

Usage:
    python config_backup_diff.py --hosts router1,router2 --username admin --password s3cr3t
    python config_backup_diff.py --inventory hosts.yaml --output-dir ./backups --show-diff
    python config_backup_diff.py --hosts 192.168.1.1 --username admin --password s3cr3t \
        --device-type cisco_ios --state-file /var/lib/net/config_hashes.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Devices must accept SSH connections with provided credentials.
"""

import argparse
import difflib
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("config_backup_diff")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_hash_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state file %s: %s", state_file, exc)
    return {}


def save_hash_state(state_file: Path, state: dict) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2))
    except OSError as exc:
        logger.error("Failed to write state file %s: %s", state_file, exc)


def backup_config(task: Task, output_dir: Path, hash_state: dict, show_diff: bool) -> Result:
    hostname = task.host.name
    try:
        result = task.run(
            task=netmiko_send_command,
            command_string="show running-config",
            use_textfsm=False,
        )
        config = result[0].result.strip()
    except Exception as exc:
        return Result(host=task.host, failed=True, exception=exc,
                      result=f"Connection failed: {exc}")

    new_hash = _sha256(config)
    prev_hash = hash_state.get(hostname, {}).get("hash")

    status = "unchanged"
    diff_output = ""

    if new_hash != prev_hash:
        status = "changed" if prev_hash else "new"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = output_dir / f"{hostname}_{timestamp}.cfg"
        try:
            backup_path.write_text(config)
            logger.info("[%s] Backup written: %s", hostname, backup_path)
        except OSError as exc:
            return Result(host=task.host, failed=True, exception=exc,
                          result=f"Write failed: {exc}")

        if show_diff and prev_hash:
            prev_path = _find_latest_backup(output_dir, hostname, exclude=backup_path)
            if prev_path:
                prev_config = prev_path.read_text().splitlines(keepends=True)
                new_lines = config.splitlines(keepends=True)
                diff_output = "".join(difflib.unified_diff(
                    prev_config, new_lines,
                    fromfile=f"{hostname} (previous)",
                    tofile=f"{hostname} (current)",
                    n=3,
                ))

        hash_state[hostname] = {
            "hash": new_hash,
            "last_changed": datetime.now().isoformat(),
            "last_backup": str(backup_path),
        }
    else:
        logger.info("[%s] Config unchanged, skipping backup", hostname)
        hash_state[hostname]["last_checked"] = datetime.now().isoformat()

    msg = f"[{hostname}] status={status}"
    if diff_output:
        msg += f"\n{diff_output}"
    return Result(host=task.host, result=msg, failed=False,
                  changed=(status != "unchanged"))


def _find_latest_backup(output_dir: Path, hostname: str, exclude: Path) -> Path | None:
    candidates = sorted(
        [p for p in output_dir.glob(f"{hostname}_*.cfg") if p != exclude],
        reverse=True,
    )
    return candidates[0] if candidates else None


def build_nornir(hosts: list[str], username: str, password: str, device_type: str):
    inventory = {
        "plugin": "SimpleInventory",
        "options": {
            "host_file": None,
            "group_file": None,
            "defaults_file": None,
        },
    }
    hosts_dict = {
        h: {
            "hostname": h,
            "username": username,
            "password": password,
            "platform": device_type,
        }
        for h in hosts
    }
    return InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {"hosts": hosts_dict, "groups": {}, "defaults": {}},
        },
        logging={"enabled": False},
    )


def main():
    parser = argparse.ArgumentParser(
        description="Differential config backup — only saves when configs change."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--hosts", help="Comma-separated list of device hostnames/IPs")
    group.add_argument("--inventory", help="Path to Nornir SimpleInventory hosts YAML")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--device-type", default="cisco_ios",
                        help="Netmiko device type (default: cisco_ios)")
    parser.add_argument("--output-dir", default="./backups",
                        help="Directory to store backup files (default: ./backups)")
    parser.add_argument("--state-file", default="./backups/.config_hashes.json",
                        help="JSON file tracking config hashes between runs")
    parser.add_argument("--show-diff", action="store_true",
                        help="Print unified diff when configs change")
    parser.add_argument("--workers", type=int, default=10,
                        help="Concurrent worker threads (default: 10)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    state_file = Path(args.state_file)
    hash_state = load_hash_state(state_file)

    if args.hosts:
        hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
        nr = build_nornir(hosts, args.username, args.password, args.device_type)
    else:
        nr = InitNornir(
            inventory={"plugin": "SimpleInventory",
                       "options": {"host_file": args.inventory}},
            logging={"enabled": False},
        )
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password

    nr.config.runner.options["num_workers"] = args.workers

    logger.info("Running differential backup on %d device(s)", len(nr.inventory.hosts))

    results = nr.run(
        task=backup_config,
        output_dir=output_dir,
        hash_state=hash_state,
        show_diff=args.show_diff,
    )

    save_hash_state(state_file, hash_state)

    changed = sum(1 for r in results.values() if r.changed)
    failed = sum(1 for r in results.values() if r.failed)
    unchanged = len(results) - changed - failed

    print(f"\n--- Backup Summary ---")
    print(f"  Changed:   {changed}")
    print(f"  Unchanged: {unchanged}")
    print(f"  Failed:    {failed}")
    print(f"  State:     {state_file}")

    if failed:
        print_result(results, vars=["exception"])
        sys.exit(1)


if __name__ == "__main__":
    main()
```