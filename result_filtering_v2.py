result_filtering_v3.py — Multi-condition Nornir result filtering with structured output

Purpose:
    Run a command across inventory hosts, then filter results by composing
    multiple independent conditions: host attributes (name/group/platform),
    task status (success/failure), and a regex match against command output.
    Renders matched rows as a plain table, JSON, or CSV. Returns exit code 1
    when matched count exceeds an optional threshold — useful as a CI/CD gate
    (e.g., alert if more than 0 devices show CPU > 80%).

Usage:
    python result_filtering_v3.py --username admin --password secret \
        --command "show processes cpu" --match "CPU.*[89][0-9]%" --alert-threshold 0

    python result_filtering_v3.py --username admin --password secret \
        --group wan_routers --failed --format json --output failures.json

    python result_filtering_v3.py --username admin --password secret \
        --platform eos --command "show interfaces status" --match "err-disabled"

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory: hosts.yaml, groups.yaml, defaults.yaml in --inventory directory
"""

import argparse
import csv
import json
import logging
import re
import sys
from io import StringIO
from typing import List, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko.tasks import netmiko_send_command


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=level)


def build_host_filter(args: argparse.Namespace) -> Optional[F]:
    filters = []
    if args.hosts:
        filters.append(F(name__contains=args.hosts) | F(hostname__contains=args.hosts))
    if args.group:
        filters.append(F(groups__contains=args.group))
    if args.platform:
        filters.append(F(platform=args.platform))

    if not filters:
        return None
    combined = filters[0]
    for f in filters[1:]:
        combined = combined & f
    return combined


def filter_results(
    results,
    pattern: Optional[str],
    failed_only: bool,
    success_only: bool,
) -> List[dict]:
    regex = re.compile(pattern, re.IGNORECASE) if pattern else None
    matched = []

    for host, multi_result in results.items():
        for result in multi_result:
            if failed_only and not result.failed:
                continue
            if success_only and result.failed:
                continue

            output = str(result.result or "")
            if regex:
                hit = regex.search(output)
                if not hit:
                    continue
                match_str = hit.group(0)
            else:
                match_str = ""

            matched.append({
                "host": host,
                "task": result.name,
                "failed": result.failed,
                "status": "FAIL" if result.failed else "OK",
                "match": match_str,
                "output": output.strip(),
            })

    return matched


def render_table(rows: List[dict]) -> str:
    if not rows:
        return "No matching results."

    headers = ["HOST", "TASK", "STATUS", "MATCH", "OUTPUT"]
    widths = {h: len(h) for h in headers}
    for row in rows:
        widths["HOST"] = max(widths["HOST"], len(row["host"]))
        widths["TASK"] = max(widths["TASK"], len(row["task"]))
        widths["STATUS"] = max(widths["STATUS"], len(row["status"]))
        widths["MATCH"] = max(widths["MATCH"], len(row["match"]))
        widths["OUTPUT"] = min(60, max(widths["OUTPUT"], len(row["output"])))

    sep = "-+-".join("-" * widths[h] for h in headers)
    header_line = " | ".join(h.ljust(widths[h]) for h in headers)
    lines = [header_line, sep]
    for row in rows:
        lines.append(" | ".join([
            row["host"].ljust(widths["HOST"]),
            row["task"].ljust(widths["TASK"]),
            row["status"].ljust(widths["STATUS"]),
            row["match"].ljust(widths["MATCH"]),
            row["output"][:60].ljust(widths["OUTPUT"]),
        ]))
    return "\n".join(lines)


def render_csv(rows: List[dict]) -> str:
    if not rows:
        return ""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=["host", "task", "failed", "status", "match", "output"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_output(content: str, path: Optional[str]) -> None:
    if path:
        with open(path, "w") as fh:
            fh.write(content)
        logging.info("Results written to %s", path)
    else:
        print(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter Nornir task results using composable host, status, and content conditions."
    )
    parser.add_argument("--command", default="show version",
                        help="CLI command to run on matched devices (default: 'show version')")
    parser.add_argument("--hosts", metavar="SUBSTR",
                        help="Filter by hostname or IP substring")
    parser.add_argument("--group", metavar="NAME",
                        help="Filter by inventory group name")
    parser.add_argument("--platform", metavar="OS",
                        help="Filter by platform (ios, eos, nxos, ...)")
    status_grp = parser.add_mutually_exclusive_group()
    status_grp.add_argument("--failed", action="store_true",
                             help="Return only failed tasks")
    status_grp.add_argument("--success", action="store_true",
                             help="Return only successful tasks")
    parser.add_argument("--match", metavar="REGEX",
                        help="Keep results whose output matches this regex (case-insensitive)")
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table",
                        dest="fmt", help="Output format (default: table)")
    parser.add_argument("--output", metavar="FILE",
                        help="Write results to FILE instead of stdout")
    parser.add_argument("--alert-threshold", type=int, default=None, metavar="N",
                        help="Exit 1 if matched result count exceeds N (0 = any match fails)")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel worker threads (default: 10)")
    parser.add_argument("--inventory", default=".",
                        help="Directory containing inventory YAML files (default: .)")
    parser.add_argument("--username", required=True, help="Device login username")
    parser.add_argument("--password", required=True, help="Device login password")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    nr = InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": f"{args.inventory}/hosts.yaml",
                "group_file": f"{args.inventory}/groups.yaml",
                "defaults_file": f"{args.inventory}/defaults.yaml",
            },
        },
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        logging={"enabled": False},
    )
    nr.inventory.defaults.username = args.username
    nr.inventory.defaults.password = args.password

    host_filter = build_host_filter(args)
    target = nr.filter(host_filter) if host_filter else nr

    if not target.inventory.hosts:
        logging.error("No hosts matched the specified filters.")
        return 1

    logging.info("Running '%s' on %d host(s)", args.command, len(target.inventory.hosts))
    results = target.run(
        name=args.command,
        task=netmiko_send_command,
        command_string=args.command,
    )

    matched = filter_results(
        results,
        pattern=args.match,
        failed_only=args.failed,
        success_only=args.success,
    )

    if args.fmt == "json":
        content = json.dumps(matched, indent=2)
    elif args.fmt == "csv":
        content = render_csv(matched)
    else:
        summary = f"\n{len(matched)} result(s) matched | {len(target.inventory.hosts)} host(s) queried"
        content = render_table(matched) + summary

    write_output(content, args.output)

    if args.alert_threshold is not None and len(matched) > args.alert_threshold:
        logging.warning(
            "Alert threshold exceeded: %d matched > %d allowed",
            len(matched),
            args.alert_threshold,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())