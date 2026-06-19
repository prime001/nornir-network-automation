Startup vs Running Config Drift Detector

Compares startup-config against running-config on each device to identify
unsaved changes. In production networks, untracked running-config changes
are lost on reload — this script surfaces those devices before it matters.

Usage:
    python 035_config_backup.py --hosts router1,router2 --username admin --password secret
    python 035_config_backup.py --hosts router1 -u admin -p secret --save-diffs --diff-dir /tmp/diffs
    python 035_config_backup.py --hosts router1 -u admin -p secret --platform cisco_nxos

Prerequisites:
    pip install nornir nornir-netmiko netmiko
"""

import argparse
import difflib
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


PLATFORM_COMMANDS = {
    "cisco_ios": ("show startup-config", "show running-config"),
    "cisco_nxos": ("show startup-config", "show running-config"),
    "cisco_xr": ("show running-config", "show running-config committed"),
    "arista_eos": ("show startup-config", "show running-config"),
    "juniper_junos": ("show configuration", "show configuration | compare rollback 0"),
}


def build_inventory(hosts: list[str], username: str, password: str, platform: str) -> Inventory:
    host_dict = {}
    for host in hosts:
        host_dict[host] = Host(
            name=host,
            hostname=host,
            username=username,
            password=password,
            platform=platform,
            groups=[],
        )
    return Inventory(hosts=Hosts(host_dict), groups=Groups({}), defaults=Defaults())


def check_config_drift(task: Task, platform: str) -> Result:
    if platform not in PLATFORM_COMMANDS:
        return Result(host=task.host, result=None, failed=True,
                      exception=ValueError(f"Unsupported platform: {platform}"))

    startup_cmd, running_cmd = PLATFORM_COMMANDS[platform]

    startup_result = task.run(task=netmiko_send_command, command_string=startup_cmd,
                              name="startup-config")
    running_result = task.run(task=netmiko_send_command, command_string=running_cmd,
                              name="running-config")

    startup = startup_result[0].result or ""
    running = running_result[0].result or ""

    startup_lines = startup.splitlines(keepends=True)
    running_lines = running.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        startup_lines,
        running_lines,
        fromfile="startup-config",
        tofile="running-config",
        lineterm="",
    ))

    return Result(
        host=task.host,
        result={
            "has_drift": len(diff) > 0,
            "diff_lines": diff,
            "startup_lines": len(startup_lines),
            "running_lines": len(running_lines),
        },
    )


def save_diff(host: str, diff_lines: list[str], diff_dir: str) -> Optional[str]:
    os.makedirs(diff_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(diff_dir, f"{host}_drift_{ts}.diff")
    with open(path, "w") as f:
        f.writelines(diff_lines)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect unsaved config changes (startup vs running drift)",
    )
    parser.add_argument("--hosts", required=True,
                        help="Comma-separated list of device hostnames/IPs")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument("--platform", default="cisco_ios",
                        choices=list(PLATFORM_COMMANDS.keys()),
                        help="Netmiko platform type (default: cisco_ios)")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel worker threads (default: 10)")
    parser.add_argument("--save-diffs", action="store_true",
                        help="Write unified diffs to files")
    parser.add_argument("--diff-dir", default="./config_diffs",
                        help="Directory for diff output (default: ./config_diffs)")
    parser.add_argument("--show-diff", action="store_true",
                        help="Print diffs to stdout")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    if not hosts:
        print("ERROR: no valid hosts provided", file=sys.stderr)
        sys.exit(1)

    inventory = build_inventory(hosts, args.username, args.password, args.platform)
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={"plugin": "SimpleInventory"},
        logging={"enabled": False},
    )
    nr.inventory = inventory

    print(f"Checking config drift on {len(hosts)} device(s) [{args.platform}]...\n")
    results = nr.run(task=check_config_drift, platform=args.platform)

    drifted, clean, failed = [], [], []

    for host, multi_result in results.items():
        if multi_result.failed:
            failed.append(host)
            logger.error("Failed on %s: %s", host, multi_result.exception)
            continue

        data = multi_result[0].result
        if data is None:
            failed.append(host)
            continue

        if data["has_drift"]:
            drifted.append(host)
            changed = sum(1 for l in data["diff_lines"] if l.startswith(("+", "-"))
                          and not l.startswith(("+++", "---")))
            print(f"  [DRIFT]  {host}  ({changed} changed lines)")
            if args.show_diff:
                print("".join(data["diff_lines"][:60]))
                if len(data["diff_lines"]) > 60:
                    print(f"  ... ({len(data['diff_lines']) - 60} more lines)")
            if args.save_diffs:
                path = save_diff(host, data["diff_lines"], args.diff_dir)
                print(f"           diff saved: {path}")
        else:
            clean.append(host)
            print(f"  [clean]  {host}")

    print(f"\nSummary: {len(drifted)} drifted / {len(clean)} clean / {len(failed)} failed")

    if drifted:
        print("\nDevices with unsaved changes (reboot risk):")
        for h in drifted:
            print(f"  - {h}")

    sys.exit(1 if drifted or failed else 0)


if __name__ == "__main__":
    main()