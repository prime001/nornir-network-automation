The existing queued script is a BGP health monitor with JSON output focused on alerting. I'll write a complementary BGP summary that produces human-readable tabular output (like `show bgp summary`) — different focus, different output format.

```python
"""
bgp_summary.py — BGP Neighbor Summary Table

Connects to one or more routers via Nornir + NAPALM and renders a
human-readable BGP neighbor summary table — similar to the output of
`show bgp summary` or `show bgp neighbors` on Cisco/Juniper/Arista.

Useful for quick NOC checks, change-window verification, and
post-maintenance validation across a fleet without logging into
each device individually.

Usage:
    python bgp_summary.py \
        --hosts  inventory/hosts.yaml   \
        --groups inventory/groups.yaml  \
        --defaults inventory/defaults.yaml \
        [--filter role=edge]            \
        [--vrf all|default|<name>]      \
        [--csv bgp_snapshot.csv]        \
        [--workers 10]                  \
        [--verbose]

Prerequisites:
    pip install nornir nornir-napalm nornir-utils napalm

    NAPALM-supported platforms: ios, eos, junos, nxos_ssh.
    Credentials live in defaults.yaml (or per-host in hosts.yaml).

Inventory quickstart (defaults.yaml):
    username: admin
    password: secret
    port: 22
    platform: ios
"""

import argparse
import csv
import logging
import sys
from io import StringIO
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logger = logging.getLogger(__name__)

COLUMN_WIDTHS = (16, 8, 20, 7, 10, 10, 9, 11)
HEADERS = ("Peer", "VRF", "Description", "AS", "Rcv-Pfx", "Snd-Pfx", "State", "Uptime")


def _fmt_uptime(seconds: int) -> str:
    if seconds < 0:
        return "never"
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d{h:02d}h{m:02d}m"
    return f"{h:02d}:{m:02d}:{s:02d}"


def _row(values) -> str:
    return "  ".join(str(v).ljust(w) for v, w in zip(values, COLUMN_WIDTHS))


def collect_bgp_summary(task: Task, vrf_filter: str) -> Result:
    """Pull bgp_neighbors from NAPALM and flatten into a list of row dicts."""
    task.run(task=napalm_get, getters=["bgp_neighbors"])
    raw = task.results[1].result.get("bgp_neighbors", {})

    rows = []
    for vrf, vrf_data in raw.items():
        if vrf_filter not in ("all", vrf):
            continue
        for peer_ip, peer in vrf_data.get("peers", {}).items():
            rows.append({
                "host": task.host.name,
                "peer": peer_ip,
                "vrf": vrf,
                "description": peer.get("description", ""),
                "remote_as": peer.get("remote_as", ""),
                "address_family": list(peer.get("address_family", {}).keys()),
                "rcv_prefixes": sum(
                    af.get("received_prefixes", 0) or 0
                    for af in peer.get("address_family", {}).values()
                ),
                "snd_prefixes": sum(
                    af.get("sent_prefixes", 0) or 0
                    for af in peer.get("address_family", {}).values()
                ),
                "state": peer.get("connection_state", "unknown"),
                "uptime": peer.get("uptime", -1) or -1,
            })

    return Result(host=task.host, result=rows)


def render_table(all_rows: list[dict]) -> str:
    buf = StringIO()
    separator = "-" * (sum(COLUMN_WIDTHS) + 2 * (len(COLUMN_WIDTHS) - 1))

    current_host = None
    for row in sorted(all_rows, key=lambda r: (r["host"], r["vrf"], r["peer"])):
        if row["host"] != current_host:
            if current_host is not None:
                buf.write("\n")
            buf.write(f"Device: {row['host']}\n")
            buf.write(separator + "\n")
            buf.write(_row(HEADERS) + "\n")
            buf.write(separator + "\n")
            current_host = row["host"]

        state = row["state"]
        buf.write(_row((
            row["peer"],
            row["vrf"],
            (row["description"] or "")[:20],
            row["remote_as"],
            row["rcv_prefixes"],
            row["snd_prefixes"],
            state,
            _fmt_uptime(row["uptime"]) if state.lower() == "established" else "—",
        )) + "\n")

    return buf.getvalue()


def write_csv(all_rows: list[dict], path: str) -> None:
    fields = ["host", "peer", "vrf", "description", "remote_as",
              "rcv_prefixes", "snd_prefixes", "state", "uptime"]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(all_rows, key=lambda r: (r["host"], r["vrf"], r["peer"])):
            row["uptime"] = row["uptime"] if row["uptime"] >= 0 else ""
            writer.writerow(row)
    logger.info("CSV written to %s", path)


def parse_host_filter(filter_str: str) -> dict:
    result = {}
    for token in filter_str.split(","):
        k, _, v = token.partition("=")
        if k.strip() and v.strip():
            result[k.strip()] = v.strip()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="BGP neighbor summary table — Nornir + NAPALM"
    )
    parser.add_argument("--hosts", default="inventory/hosts.yaml", metavar="FILE")
    parser.add_argument("--groups", default="inventory/groups.yaml", metavar="FILE")
    parser.add_argument("--defaults", default="inventory/defaults.yaml", metavar="FILE")
    parser.add_argument(
        "--filter", metavar="KEY=VAL[,KEY=VAL]",
        help="Nornir host filter (e.g. role=edge,site=nyc)",
    )
    parser.add_argument(
        "--vrf", default="all", metavar="NAME",
        help="VRF to display: 'all' (default), 'default', or a specific VRF name",
    )
    parser.add_argument("--csv", metavar="FILE", help="Also write results to a CSV file")
    parser.add_argument("--workers", type=int, default=10, metavar="N")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    for inv_file in (args.hosts, args.groups, args.defaults):
        if not Path(inv_file).exists():
            logger.error("Inventory file not found: %s", inv_file)
            sys.exit(2)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.hosts,
                "group_file": args.groups,
                "defaults_file": args.defaults,
            },
        },
        logging={"enabled": False},
    )

    if args.filter:
        nr = nr.filter(F(**parse_host_filter(args.filter)))

    if not nr.inventory.hosts:
        print("No hosts matched — check your inventory and --filter expression.", file=sys.stderr)
        sys.exit(2)

    results = nr.run(task=collect_bgp_summary, vrf_filter=args.vrf)

    all_rows = []
    failed_hosts = []
    for host, multi_result in results.items():
        if multi_result.failed:
            failed_hosts.append((host, str(multi_result.exception)))
        else:
            all_rows.extend(multi_result[0].result)

    if all_rows:
        print(render_table(all_rows))

    if args.csv and all_rows:
        write_csv(all_rows, args.csv)

    for host, err in failed_hosts:
        print(f"ERROR  {host}: {err}", file=sys.stderr)

    if not all_rows:
        print("No BGP neighbor data returned.", file=sys.stderr)
        sys.exit(1)

    if failed_hosts:
        sys.exit(1)


if __name__ == "__main__":
    main()
```