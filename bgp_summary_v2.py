```python
"""
BGP Advertised Prefix Auditor

Connects to routers via Nornir and audits which prefixes are being advertised
to each BGP neighbor, comparing against an expected prefix policy file. Reports
unexpected advertisements and missing required prefixes per neighbor.

Usage:
    python 043_bgp_prefix_audit.py --hosts router1,router2 --policy policy.yaml
    python 043_bgp_prefix_audit.py --hosts router1 --neighbor 10.0.0.1 --output json
    python 043_bgp_prefix_audit.py --inventory hosts.yaml --output csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko pyyaml
    A Nornir inventory file (hosts.yaml / groups.yaml) or use --hosts for ad-hoc.
    Optional: a policy YAML file with allowed_prefixes per neighbor IP.

Policy file format (policy.yaml):
    neighbors:
      "10.0.0.1":
        allowed_prefixes:
          - "192.168.1.0/24"
          - "10.1.0.0/16"
      default:
        allowed_prefixes: []   # empty = allow all
"""

import argparse
import csv
import json
import logging
import re
import sys
from typing import Optional

import yaml
from nornir import InitNornir
from nornir.core import Nornir
from nornir.core.inventory import (
    Defaults,
    Groups,
    Host,
    Hosts,
    Inventory,
)
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_policy(policy_path: Optional[str]) -> dict:
    if not policy_path:
        return {}
    try:
        with open(policy_path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Failed to load policy file %s: %s", policy_path, exc)
        sys.exit(1)


def parse_advertised_routes(output: str) -> list:
    prefixes = []
    for line in output.splitlines():
        # IOS: status codes then network, e.g. "*> 192.168.1.0/24  ..."
        match = re.match(
            r"^\s*[*idshrSDb>i ]{1,5}\s+(\d+\.\d+\.\d+\.\d+(?:/\d+)?)", line
        )
        if match:
            prefix = match.group(1)
            if "/" not in prefix:
                prefix += "/32"
            prefixes.append(prefix)
    return list(set(prefixes))


def collect_bgp_neighbors(task: Task) -> Result:
    r = task.run(
        task=netmiko_send_command, command_string="show ip bgp summary"
    )
    neighbors = []
    in_table = False
    for line in r.result.splitlines():
        if re.match(r"^Neighbor\s+V\s+AS", line):
            in_table = True
            continue
        if in_table:
            m = re.match(r"^(\d+\.\d+\.\d+\.\d+)\s", line)
            if m:
                neighbors.append(m.group(1))
    return Result(host=task.host, result=neighbors)


def audit_neighbor(task: Task, neighbor_ip: str, policy: dict) -> Result:
    cmd = f"show ip bgp neighbors {neighbor_ip} advertised-routes"
    r = task.run(task=netmiko_send_command, command_string=cmd)
    advertised = parse_advertised_routes(r.result)

    nbr_policy = (
        policy.get("neighbors", {}).get(neighbor_ip)
        or policy.get("neighbors", {}).get("default")
        or {}
    )
    allowed = set(nbr_policy.get("allowed_prefixes", []))

    if allowed:
        unexpected = sorted(set(advertised) - allowed)
        missing = sorted(allowed - set(advertised))
    else:
        unexpected = []
        missing = []

    return Result(
        host=task.host,
        result={
            "host": task.host.name,
            "neighbor": neighbor_ip,
            "advertised_count": len(advertised),
            "advertised": sorted(advertised),
            "unexpected": unexpected,
            "missing": missing,
            "compliant": not unexpected and not missing,
        },
    )


def build_adhoc_nornir(
    hosts: list, username: str, password: str, platform: str
) -> Nornir:
    host_objects = {
        h: Host(
            name=h,
            hostname=h,
            username=username,
            password=password,
            platform=platform,
            data={},
            groups=[],
            defaults=Defaults(),
            connection_options={},
        )
        for h in hosts
    }
    inv = Inventory(
        hosts=Hosts(host_objects), groups=Groups(), defaults=Defaults()
    )
    return Nornir(inventory=inv, runner=None, processors=[], data={}, config=None)


def render_table(audits: list, has_policy: bool) -> None:
    for a in audits:
        status = "PASS" if a["compliant"] else "FAIL"
        print(f"\n[{status}] {a['host']} -> neighbor {a['neighbor']}")
        print(f"  Advertised: {a['advertised_count']} prefixes")
        if a["unexpected"]:
            print(f"  UNEXPECTED ({len(a['unexpected'])}):")
            for p in a["unexpected"]:
                print(f"    - {p}")
        if a["missing"]:
            print(f"  MISSING ({len(a['missing'])}):")
            for p in a["missing"]:
                print(f"    - {p}")
        if not has_policy and a["advertised"]:
            sample = a["advertised"][:6]
            tail = " ..." if len(a["advertised"]) > 6 else ""
            print(f"  Sample: {', '.join(sample)}{tail}")


def render_csv(audits: list) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        ["host", "neighbor", "advertised_count", "compliant", "unexpected", "missing"]
    )
    for a in audits:
        writer.writerow([
            a["host"],
            a["neighbor"],
            a["advertised_count"],
            a["compliant"],
            "; ".join(a["unexpected"]),
            "; ".join(a["missing"]),
        ])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit BGP advertised prefixes against a policy file."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--hosts", help="Comma-separated list of router IPs or hostnames"
    )
    src.add_argument("--inventory", help="Nornir config YAML pointing to inventory")
    parser.add_argument("--username", "-u", default="admin")
    parser.add_argument("--password", "-p", default="admin")
    parser.add_argument(
        "--platform", default="cisco_ios", help="Netmiko platform string"
    )
    parser.add_argument(
        "--neighbor", help="Audit only this neighbor IP (skips auto-discovery)"
    )
    parser.add_argument("--policy", help="YAML policy file with allowed prefixes")
    parser.add_argument(
        "--output", choices=["table", "json", "csv"], default="table"
    )
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    policy = load_policy(args.policy)

    if args.inventory:
        nr = InitNornir(config_file=args.inventory)
    else:
        host_list = [h.strip() for h in args.hosts.split(",")]
        nr = build_adhoc_nornir(
            host_list, args.username, args.password, args.platform
        )

    from nornir.core.plugins.runners import ThreadedRunner

    nr.runner = ThreadedRunner(num_workers=args.workers)

    all_audits = []

    for hostname in nr.inventory.hosts:
        host_nr = nr.filter(name=hostname)

        if args.neighbor:
            neighbor_list = [args.neighbor]
        else:
            nbr_result = host_nr.run(task=collect_bgp_neighbors)
            neighbor_list = nbr_result[hostname][0].result or []

        if not neighbor_list:
            logger.warning("No BGP neighbors found on %s", hostname)
            continue

        for nbr_ip in neighbor_list:
            result = host_nr.run(
                task=audit_neighbor, neighbor_ip=nbr_ip, policy=policy
            )
            audit = result[hostname][0].result
            if isinstance(audit, dict):
                all_audits.append(audit)

    if args.output == "json":
        print(json.dumps(all_audits, indent=2))
    elif args.output == "csv":
        render_csv(all_audits)
    else:
        render_table(all_audits, bool(policy))

    failures = [a for a in all_audits if not a["compliant"]]
    if failures:
        logger.warning("%d neighbor(s) failed policy audit", len(failures))
        sys.exit(2)


if __name__ == "__main__":
    main()
```