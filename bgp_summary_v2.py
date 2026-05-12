BGP Route Analysis and AS-Path Audit

Purpose:
    Collects the full BGP routing table from one or more devices and performs
    AS-path depth analysis, transit-ASN filtering, and community-string auditing.
    Useful for validating route policy correctness, identifying sub-optimal paths,
    and confirming community tagging compliance across the network.

Usage:
    python bgp_route_audit.py --hosts rtr1,rtr2 --username admin --password secret
    python bgp_route_audit.py --inventory hosts.yaml --filter-asn 65001
    python bgp_route_audit.py --hosts 10.0.0.1 --max-aspath 5 --require-community 65000:100 --export out.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Devices must support IOS-style: show ip bgp
"""

import argparse
import csv
import logging
import re
import sys
from dataclasses import dataclass, field

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_BGP_ROUTE_RE = re.compile(
    r"^(?P<flags>[*>sidhrSRH ]{2})\s+"
    r"(?P<prefix>\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}|\s{1,20})"
    r"\s+(?P<next_hop>\d{1,3}(?:\.\d{1,3}){3})"
    r"\s+(?P<metric>\d+)\s+(?P<locpref>\d+)\s+\d+"
    r"\s*(?P<aspath>[\d ]*)\s*(?P<origin>[ie?])\s*$",
    re.MULTILINE,
)


@dataclass
class BgpRoute:
    prefix: str
    next_hop: str
    as_path: list
    local_pref: int
    med: int
    origin: str
    best: bool
    communities: list = field(default_factory=list)


def parse_bgp_table(output: str) -> list:
    routes = []
    current_prefix = ""
    for m in _BGP_ROUTE_RE.finditer(output):
        raw_prefix = m.group("prefix").strip()
        if raw_prefix:
            current_prefix = raw_prefix
        if not current_prefix:
            continue
        aspath_raw = m.group("aspath").strip()
        routes.append(
            BgpRoute(
                prefix=current_prefix,
                next_hop=m.group("next_hop"),
                as_path=aspath_raw.split() if aspath_raw else [],
                local_pref=int(m.group("locpref") or 100),
                med=int(m.group("metric") or 0),
                origin=m.group("origin"),
                best=">" in m.group("flags"),
            )
        )
    return routes


def collect_bgp_routes(task: Task) -> Result:
    result = task.run(
        task=netmiko_send_command,
        command_string="show ip bgp",
        name="show ip bgp",
    )
    routes = parse_bgp_table(result.result)
    logger.debug("%s: parsed %d BGP routes", task.host, len(routes))
    return Result(host=task.host, result=routes)


def analyze(routes: list, filter_asn, max_aspath, require_community) -> dict:
    best = [r for r in routes if r.best]
    lengths = [len(r.as_path) for r in best]
    findings = {
        "total": len(routes),
        "best_paths": len(best),
        "avg_aspath_len": round(sum(lengths) / len(lengths), 2) if lengths else 0,
        "long_paths": [],
        "transit_asn_routes": [],
        "missing_community": [],
    }
    for r in best:
        if max_aspath and len(r.as_path) > max_aspath:
            findings["long_paths"].append(
                {"prefix": r.prefix, "hops": len(r.as_path), "path": " ".join(r.as_path)}
            )
        if filter_asn and filter_asn in r.as_path:
            findings["transit_asn_routes"].append(
                {"prefix": r.prefix, "path": " ".join(r.as_path)}
            )
        if require_community and require_community not in r.communities:
            findings["missing_community"].append(r.prefix)
    return findings


def print_report(hostname: str, findings: dict, filter_asn, max_aspath) -> None:
    print(f"\n{'=' * 62}")
    print(f"  Host: {hostname}")
    print(f"  Total routes        : {findings['total']}")
    print(f"  Best paths          : {findings['best_paths']}")
    print(f"  Avg AS-path length  : {findings['avg_aspath_len']}")

    if max_aspath and findings["long_paths"]:
        print(f"\n  Paths exceeding {max_aspath} hop(s) — {len(findings['long_paths'])} found:")
        for e in findings["long_paths"]:
            print(f"    {e['prefix']:<22} hops={e['hops']}  {e['path']}")

    if filter_asn and findings["transit_asn_routes"]:
        print(f"\n  Routes transiting AS{filter_asn} — {len(findings['transit_asn_routes'])} found:")
        for e in findings["transit_asn_routes"]:
            print(f"    {e['prefix']:<22} {e['path']}")

    if findings["missing_community"]:
        print(f"\n  Prefixes missing required community — {len(findings['missing_community'])} found:")
        for prefix in findings["missing_community"]:
            print(f"    {prefix}")


def export_csv(all_results: dict, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["host", "metric", "value"])
        for host, f in all_results.items():
            for key in ("total", "best_paths", "avg_aspath_len"):
                w.writerow([host, key, f[key]])
            w.writerow([host, "long_path_count", len(f["long_paths"])])
            w.writerow([host, "transit_asn_count", len(f["transit_asn_routes"])])
            w.writerow([host, "missing_community_count", len(f["missing_community"])])
    logger.info("Results exported to %s", path)


def build_nornir(args) -> object:
    runner_cfg = {"plugin": "threaded", "options": {"num_workers": args.workers}}
    if args.inventory:
        return InitNornir(
            runner=runner_cfg,
            inventory={"plugin": "SimpleInventory", "options": {"host_file": args.inventory}},
        )
    hosts = {}
    for raw in args.hosts.split(","):
        h = raw.strip()
        hosts[h] = Host(
            name=h,
            hostname=h,
            username=args.username,
            password=args.password,
            platform=args.platform,
            port=args.port,
        )
    return InitNornir(
        runner=runner_cfg,
        inventory=Inventory(hosts=Hosts(hosts), groups=Groups(), defaults=Defaults()),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BGP route analysis and AS-path audit via Nornir")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--hosts", metavar="HOST[,HOST]", help="Comma-separated hostnames or IPs")
    grp.add_argument("--inventory", metavar="FILE", help="Nornir SimpleInventory hosts.yaml")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="admin")
    p.add_argument("--platform", default="cisco_ios")
    p.add_argument("--port", type=int, default=22)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--filter-asn", metavar="ASN", help="Report all best paths transiting this ASN")
    p.add_argument("--max-aspath", type=int, metavar="N", help="Flag best paths longer than N hops")
    p.add_argument(
        "--require-community", metavar="X:Y",
        help="Flag best paths that do not carry this community tag"
    )
    p.add_argument("--export", metavar="FILE", help="Write per-host summary to CSV")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = build_nornir(args)
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        sys.exit(1)

    logger.info("Auditing BGP tables on %d device(s)", len(nr.inventory.hosts))
    agg = nr.run(task=collect_bgp_routes, name="bgp_route_audit")

    all_findings: dict = {}
    for host, multi in agg.items():
        if multi.failed:
            logger.error("%s failed: %s", host, multi.exception)
            continue
        routes = multi[0].result
        findings = analyze(routes, args.filter_asn, args.max_aspath, args.require_community)
        all_findings[host] = findings
        print_report(host, findings, args.filter_asn, args.max_aspath)

    if args.export and all_findings:
        export_csv(all_findings, args.export)

    if any(m.failed for m in agg.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()