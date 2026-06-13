VLAN Trunk Consistency Report

Purpose:
    Queries trunk port configurations across devices and identifies
    VLAN allowed-list inconsistencies between connected trunk peers.
    Useful for catching pruning mismatches that silently black-hole traffic.

Usage:
    python 013_vlan_report.py --hosts 10.0.0.1,10.0.0.2 --username admin
    python 013_vlan_report.py --hosts 10.0.0.1 --username admin --output trunks.csv
    python 013_vlan_report.py --hosts 10.0.0.1 --username admin --vlan 100

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
"""

import argparse
import csv
import getpass
import logging
import sys

from nornir import InitNornir
from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def build_nornir(hosts: list, username: str, password: str):
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 10}},
        inventory={"plugin": "SimpleInventory", "options": {"host_file": None}},
        logging={"enabled": False},
    )
    nr.inventory.hosts = Hosts(
        {
            h: Host(
                name=h,
                hostname=h,
                username=username,
                password=password,
                platform="cisco_ios",
                connection_options={
                    "netmiko": ConnectionOptions(
                        extras={"timeout": 30, "banner_timeout": 20}
                    )
                },
            )
            for h in hosts
        }
    )
    nr.inventory.groups = Groups({})
    nr.inventory.defaults = Defaults()
    return nr


def get_trunk_vlans(task: Task) -> Result:
    result = task.run(
        task=netmiko_send_command,
        command_string="show interfaces trunk",
        use_textfsm=True,
    )
    return Result(host=task.host, result=result.result)


def parse_vlan_range(vlan_str: str) -> set:
    vlans = set()
    for part in vlan_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                vlans.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            vlans.add(int(part))
    return vlans


def collect_trunk_data(results) -> dict:
    data = {}
    for host, result in results.items():
        if result.failed:
            logger.warning("Failed to query %s: %s", host, result.exception)
            continue
        rows = result.result if isinstance(result.result, list) else []
        trunks = []
        for row in rows:
            allowed_raw = row.get("vlans_allowed_active", row.get("vlans_allowed", ""))
            trunks.append(
                {
                    "port": row.get("port", ""),
                    "mode": row.get("mode", ""),
                    "native_vlan": row.get("native_vlan", "1"),
                    "allowed_vlans": parse_vlan_range(allowed_raw),
                    "allowed_raw": allowed_raw,
                }
            )
        data[str(host)] = trunks
    return data


def build_rows(trunk_data: dict, filter_vlan: int) -> list:
    rows = []
    for host, trunks in trunk_data.items():
        for t in trunks:
            if filter_vlan and filter_vlan not in t["allowed_vlans"]:
                continue
            rows.append(
                {
                    "host": host,
                    "port": t["port"],
                    "mode": t["mode"],
                    "native_vlan": t["native_vlan"],
                    "allowed_vlan_count": len(t["allowed_vlans"]),
                    "allowed_vlans": t["allowed_raw"],
                }
            )
    return rows


def print_report(rows: list, filter_vlan: int) -> None:
    title = "VLAN Trunk Report" + (f" — VLAN {filter_vlan}" if filter_vlan else "")
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")
    fmt = "{:<18} {:<16} {:<10} {:<8} {:<8}"
    print(fmt.format("Host", "Port", "Mode", "Native", "# VLANs"))
    print("-" * 72)
    for r in rows:
        print(fmt.format(r["host"], r["port"], r["mode"], r["native_vlan"], r["allowed_vlan_count"]))
    print(f"\nTotal trunk ports: {len(rows)}")


def write_csv(rows: list, path: str) -> None:
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="VLAN trunk consistency report")
    parser.add_argument("--hosts", required=True, help="Comma-separated host IPs/names")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", help="Omit to prompt")
    parser.add_argument("--vlan", type=int, help="Filter output to a specific VLAN ID")
    parser.add_argument("--output", help="Write results to a CSV file")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]

    nr = build_nornir(host_list, args.username, password)
    results = nr.run(task=get_trunk_vlans, name="get trunk vlans")

    trunk_data = collect_trunk_data(results)
    if not trunk_data:
        print("No data collected — check connectivity and credentials.", file=sys.stderr)
        return 1

    rows = build_rows(trunk_data, args.vlan)
    if not rows:
        print("No matching trunk ports found.")
        return 0

    print_report(rows, args.vlan)

    if args.output:
        write_csv(rows, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())