```python
"""
BGP Prefix Threshold Monitor — nornir-network-automation

Connects to routers via Nornir/Netmiko, parses BGP summary output, and
evaluates each neighbor's received-prefix count against configurable min/max
thresholds. Exits non-zero when violations are found, making it pipeline-safe.

Usage:
    python bgp_prefix_monitor.py --hosts-file hosts.yaml --groups-file groups.yaml \\
        --defaults-file defaults.yaml --min-prefixes 1 --max-prefixes 800000 \\
        --output violations.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Inventory files: hosts.yaml, groups.yaml, defaults.yaml (SimpleInventory format)
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from typing import Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@dataclass
class NeighborViolation:
    host: str
    neighbor: str
    as_number: str
    state_or_pfx_count: str
    min_threshold: int
    max_threshold: int
    violation: str


def parse_bgp_summary(output: str) -> list[dict]:
    """Extract neighbor rows from IOS/IOS-XE 'show bgp summary' output."""
    neighbors = []
    in_neighbor_table = False
    header_pattern = re.compile(r"Neighbor\s+V\s+AS\s+")

    for line in output.splitlines():
        if header_pattern.search(line):
            in_neighbor_table = True
            continue
        if not in_neighbor_table:
            continue
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        # First token must look like an IP address
        if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", parts[0]):
            continue
        neighbors.append({
            "neighbor": parts[0],
            "as_number": parts[2],
            "state_or_pfx_count": parts[-1],
        })
    return neighbors


def check_bgp_prefixes(
    task: Task,
    min_prefixes: int,
    max_prefixes: int,
) -> Result:
    cmd_result = task.run(
        task=netmiko_send_command,
        command_string="show bgp ipv4 unicast summary",
        name="show bgp summary",
    )
    output = cmd_result.result
    neighbors = parse_bgp_summary(output)

    violations: list[NeighborViolation] = []
    for nbr in neighbors:
        raw = nbr["state_or_pfx_count"]
        if not raw.isdigit():
            violations.append(NeighborViolation(
                host=task.host.name,
                neighbor=nbr["neighbor"],
                as_number=nbr["as_number"],
                state_or_pfx_count=raw,
                min_threshold=min_prefixes,
                max_threshold=max_prefixes,
                violation=f"neighbor not established (state={raw})",
            ))
            continue
        pfx_count = int(raw)
        if pfx_count < min_prefixes:
            violations.append(NeighborViolation(
                host=task.host.name,
                neighbor=nbr["neighbor"],
                as_number=nbr["as_number"],
                state_or_pfx_count=raw,
                min_threshold=min_prefixes,
                max_threshold=max_prefixes,
                violation=f"prefix count {pfx_count} below minimum {min_prefixes}",
            ))
        elif pfx_count > max_prefixes:
            violations.append(NeighborViolation(
                host=task.host.name,
                neighbor=nbr["neighbor"],
                as_number=nbr["as_number"],
                state_or_pfx_count=raw,
                min_threshold=min_prefixes,
                max_threshold=max_prefixes,
                violation=f"prefix count {pfx_count} exceeds maximum {max_prefixes}",
            ))

    return Result(host=task.host, result=violations)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BGP prefix threshold monitor using Nornir"
    )
    parser.add_argument("--hosts-file", default="hosts.yaml")
    parser.add_argument("--groups-file", default="groups.yaml")
    parser.add_argument("--defaults-file", default="defaults.yaml")
    parser.add_argument(
        "--min-prefixes", type=int, default=1,
        help="Minimum expected prefixes per neighbor (default: 1)",
    )
    parser.add_argument(
        "--max-prefixes", type=int, default=900000,
        help="Maximum expected prefixes per neighbor (default: 900000)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Write violations JSON to this file (optional)",
    )
    parser.add_argument(
        "--filter-host", default=None,
        help="Limit run to a single hostname from inventory",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.hosts_file,
                "group_file": args.groups_file,
                "defaults_file": args.defaults_file,
            },
        }
    )

    if args.filter_host:
        nr = nr.filter(name=args.filter_host)
        if not nr.inventory.hosts:
            log.error("Host '%s' not found in inventory.", args.filter_host)
            return 2

    log.info(
        "Running BGP prefix check on %d host(s) "
        "[min=%d, max=%d]",
        len(nr.inventory.hosts),
        args.min_prefixes,
        args.max_prefixes,
    )

    results = nr.run(
        task=check_bgp_prefixes,
        min_prefixes=args.min_prefixes,
        max_prefixes=args.max_prefixes,
    )

    all_violations: list[dict] = []
    for host, multi_result in results.items():
        if multi_result.failed:
            log.error("Task failed for host %s: %s", host, multi_result[0].exception)
            continue
        host_violations: list[NeighborViolation] = multi_result[1].result
        for v in host_violations:
            all_violations.append(asdict(v))
            log.warning("[%s] %s AS%s — %s", v.host, v.neighbor, v.as_number, v.violation)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(all_violations, fh, indent=2)
        log.info("Wrote %d violation(s) to %s", len(all_violations), args.output)

    if all_violations:
        log.error("VIOLATIONS FOUND: %d total across all hosts.", len(all_violations))
        return 1

    log.info("All BGP neighbors within thresholds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```