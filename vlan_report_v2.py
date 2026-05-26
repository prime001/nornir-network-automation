Now I understand what both existing scripts do. I'll write a VLAN trunk consistency checker — comparing trunk-allowed VLAN lists against the device's VLAN database to surface phantom and stranded VLANs. This is clearly distinct from both existing scripts.

"""
vlan_trunk_audit.py — VLAN Trunk Consistency Audit

Connects to Cisco IOS switches via Netmiko, collects trunk interface state
and the VLAN database, then cross-references them to expose:

  * Phantom VLANs  — allowed on a trunk but not defined in the VLAN DB
  * Stranded VLANs — defined in the VLAN DB but not allowed on any trunk
  * Native VLAN mismatches — trunks whose native VLAN is not in the DB

These gaps cause silent traffic drops and are easy to miss during manual review.

Usage:
    python vlan_trunk_audit.py --host 192.168.1.10 --username admin --password s3cr3t
    python vlan_trunk_audit.py --inventory hosts.yaml --format json --output audit.json
    python vlan_trunk_audit.py --host 10.0.0.1 -u admin -p pass --interface Gi0/1

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
    Inventory file format: YAML with hostname/platform/username/password fields
"""

import argparse
import json
import logging
import re
import sys
from io import StringIO
from typing import Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _expand_vlan_range(spec: str) -> set[int]:
    vlans: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, _, hi = token.partition("-")
            vlans.update(range(int(lo), int(hi) + 1))
        elif token.isdigit():
            vlans.add(int(token))
    return vlans


def _parse_trunk_output(raw: str) -> list[dict]:
    trunks = []
    port_block_re = re.compile(
        r"^(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)",
        re.MULTILINE,
    )
    allowed_section = re.search(
        r"Port\s+VLANs allowed on trunk\n(.*?)\n\n",
        raw,
        re.DOTALL,
    )
    allowed_map: dict[str, set[int]] = {}
    if allowed_section:
        for line in allowed_section.group(1).splitlines():
            parts = line.split()
            if len(parts) >= 2:
                allowed_map[parts[0]] = _expand_vlan_range(parts[1])

    for m in port_block_re.finditer(raw.split("Port      Vlans allowed")[0]):
        iface, mode, native_str = m.group(1), m.group(2), m.group(3)
        if mode not in ("trunk", "802.1q-trunk"):
            continue
        trunks.append({
            "interface": iface,
            "native_vlan": int(native_str),
            "allowed": allowed_map.get(iface, set()),
        })
    return trunks


def _parse_vlan_db(raw: str) -> set[int]:
    defined: set[int] = set()
    for line in raw.splitlines():
        m = re.match(r"^(\d{1,4})\s", line)
        if m:
            defined.add(int(m.group(1)))
    return defined


def audit_trunks(task: Task, iface_filter: Optional[str] = None) -> Result:
    trunk_raw = task.run(
        task=netmiko_send_command,
        command_string="show interfaces trunk",
    ).result

    vlan_raw = task.run(
        task=netmiko_send_command,
        command_string="show vlan brief",
    ).result

    trunks = _parse_trunk_output(trunk_raw)
    vlan_db = _parse_vlan_db(vlan_raw)

    if iface_filter:
        trunks = [t for t in trunks if iface_filter.lower() in t["interface"].lower()]

    all_allowed: set[int] = set()
    for trunk in trunks:
        all_allowed |= trunk["allowed"]

    findings: list[dict] = []
    for trunk in trunks:
        phantom = sorted(trunk["allowed"] - vlan_db)
        findings.append({
            "interface": trunk["interface"],
            "native_vlan": trunk["native_vlan"],
            "native_not_in_db": trunk["native_vlan"] not in vlan_db,
            "allowed_count": len(trunk["allowed"]),
            "phantom_vlans": phantom,
        })

    stranded = sorted(vlan_db - all_allowed - {1})

    return Result(
        host=task.host,
        result={
            "trunks": findings,
            "stranded_vlans": stranded,
            "vlan_db_count": len(vlan_db),
        },
    )


def render_table(hostname: str, data: dict) -> str:
    buf = StringIO()
    sep = "=" * 72
    buf.write(f"\n{sep}\n{hostname}\n{sep}\n")

    if not data["trunks"]:
        buf.write("  No trunk interfaces found.\n")
    else:
        buf.write(f"  {'Interface':<20} {'Native':<8} {'Allowed':>7}  {'Phantom VLANs'}\n")
        buf.write("  " + "-" * 70 + "\n")
        for t in data["trunks"]:
            native_flag = f"{t['native_vlan']} [!]" if t["native_not_in_db"] else str(t["native_vlan"])
            phantom_str = ", ".join(str(v) for v in t["phantom_vlans"]) or "—"
            buf.write(
                f"  {t['interface']:<20} {native_flag:<8} {t['allowed_count']:>7}  {phantom_str}\n"
            )

    if data["stranded_vlans"]:
        buf.write(f"\n  Stranded VLANs (in DB, not on any trunk): "
                  f"{', '.join(str(v) for v in data['stranded_vlans'])}\n")
    else:
        buf.write("\n  No stranded VLANs.\n")

    phantom_total = sum(len(t["phantom_vlans"]) for t in data["trunks"])
    buf.write(f"\n  VLAN DB size: {data['vlan_db_count']}  |  "
              f"Phantom entries: {phantom_total}  |  "
              f"Stranded VLANs: {len(data['stranded_vlans'])}\n")
    return buf.getvalue()


def build_single_host_nr(args: argparse.Namespace):
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 1}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": "/dev/stdin",
                "group_file": "/dev/null",
                "defaults_file": "/dev/null",
            },
        },
        logging={"enabled": False},
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit VLAN trunk consistency on Cisco IOS switches"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--host", metavar="IP", help="Single device hostname or IP")
    src.add_argument("--inventory", metavar="FILE", help="Nornir SimpleInventory hosts file")

    parser.add_argument("-u", "--username", default="admin")
    parser.add_argument("-p", "--password", default="")
    parser.add_argument("--platform", default="cisco_ios")
    parser.add_argument("--interface", metavar="NAME", help="Limit to a specific trunk interface")
    parser.add_argument("--filter", metavar="KEY=VAL", help="Inventory filter (e.g. site=nyc)")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--output", metavar="FILE", help="Write output to FILE instead of stdout")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.host:
        import tempfile, yaml  # noqa: E401
        hosts_data = {
            args.host: {
                "hostname": args.host,
                "platform": args.platform,
                "username": args.username,
                "password": args.password,
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            yaml.dump(hosts_data, tf)
            hosts_file = tf.name
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": 1}},
            inventory={
                "plugin": "SimpleInventory",
                "options": {"host_file": hosts_file, "group_file": "/dev/null",
                            "defaults_file": "/dev/null"},
            },
            logging={"enabled": False},
        )
    else:
        nr = InitNornir(
            runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
            inventory={
                "plugin": "SimpleInventory",
                "options": {"host_file": args.inventory, "group_file": "/dev/null",
                            "defaults_file": "/dev/null"},
            },
            logging={"enabled": False},
        )
        if args.filter:
            k, _, v = args.filter.partition("=")
            nr = nr.filter(F(**{k.strip(): v.strip()}))

    if not nr.inventory.hosts:
        logger.error("No hosts to poll — check --host or --inventory/--filter.")
        sys.exit(2)

    logger.info("Auditing %d host(s)...", len(nr.inventory.hosts))
    results = nr.run(
        task=audit_trunks,
        name="audit_trunks",
        iface_filter=args.interface,
    )

    report: dict = {}
    any_failed = False
    for host, multi in results.items():
        if multi.failed:
            logger.error("%-20s FAILED — %s", host, multi.exception)
            any_failed = True
            continue
        report[host] = multi[0].result

    if args.format == "table":
        output = "".join(render_table(h, d) for h, d in sorted(report.items()))
    else:
        output = json.dumps(report, indent=2)

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(output)
        logger.info("Report written to %s", args.output)
    else:
        print(output)

    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()