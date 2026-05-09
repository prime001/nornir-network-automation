The user wants the script output directly. I'll write it now.

```
"""
CDP/LLDP Network Topology Discovery

Collects neighbor discovery protocol data (CDP or LLDP) from one or more seed
devices via Nornir/Netmiko, builds a layer-2 topology map, and writes it as
JSON. An optional Graphviz DOT file can also be emitted for visualization
with `dot -Tpng topology.dot -o topology.png`.

Usage:
    python topology_discovery.py \
        --hosts 10.0.0.1,10.0.0.2 \
        --username admin --password secret \
        --protocol cdp --output topology.json --dot topology.dot

Prerequisites:
    pip install nornir nornir-netmiko netmiko
    CDP or LLDP must be enabled on target devices.
    User account requires at minimum read-only (show) access.
"""

import argparse
import json
import logging
import re
import sys
from typing import Any

from nornir.core import Nornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.plugins.runners import ThreadedRunner
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("topology_discovery")


def build_nornir(
    hosts: list[str], username: str, password: str, platform: str, workers: int
) -> Nornir:
    host_dict = {
        h: Host(name=h, hostname=h, username=username, password=password, platform=platform)
        for h in hosts
    }
    return Nornir(
        inventory=Inventory(hosts=Hosts(host_dict), groups=Groups({}), defaults=Defaults()),
        runner=ThreadedRunner(num_workers=workers),
    )


def collect_cdp(task: Task) -> Result:
    r = task.run(
        task=netmiko_send_command,
        command_string="show cdp neighbors detail",
        use_textfsm=True,
    )
    return Result(host=task.host, result=r.result)


def collect_lldp(task: Task) -> Result:
    r = task.run(
        task=netmiko_send_command,
        command_string="show lldp neighbors detail",
        use_textfsm=True,
    )
    return Result(host=task.host, result=r.result)


def _parse_cdp_raw(text: str) -> list[dict]:
    """Regex fallback when TextFSM template is unavailable."""
    neighbors = []
    for block in re.split(r"-{20,}", text):
        device_id = re.search(r"Device ID:\s*(\S+)", block)
        local_if = re.search(r"Interface:\s*(\S+),", block)
        remote_if = re.search(r"Port ID \(outgoing port\):\s*(\S+)", block)
        platform = re.search(r"Platform:\s*(.+?),", block)
        if device_id and local_if:
            neighbors.append(
                {
                    "neighbor": device_id.group(1),
                    "local_interface": local_if.group(1),
                    "neighbor_interface": remote_if.group(1) if remote_if else "unknown",
                    "platform": platform.group(1).strip() if platform else "unknown",
                }
            )
    return neighbors


def normalize(raw: Any, protocol: str) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        return _parse_cdp_raw(raw) if protocol == "cdp" else []
    return []


def build_topology(data: dict[str, list[dict]]) -> dict:
    topology: dict = {"nodes": {}, "edges": []}
    seen: set = set()

    for hostname, neighbors in data.items():
        topology["nodes"].setdefault(hostname, {"hostname": hostname})
        for nbr in neighbors:
            nid = nbr.get("neighbor") or nbr.get("dest_host", "unknown")
            local = nbr.get("local_interface") or nbr.get("local_port", "")
            remote = nbr.get("neighbor_interface") or nbr.get("neighbor_port", "")
            plat = nbr.get("platform") or nbr.get("capabilities", "")

            topology["nodes"].setdefault(nid, {"hostname": nid, "platform": plat})

            key = tuple(sorted([f"{hostname}:{local}", f"{nid}:{remote}"]))
            if key not in seen:
                seen.add(key)
                topology["edges"].append(
                    {
                        "source": hostname,
                        "source_interface": local,
                        "target": nid,
                        "target_interface": remote,
                    }
                )

    return topology


def write_dot(topology: dict, path: str) -> None:
    lines = ['graph topology {', '    rankdir=LR;', '    node [shape=box];']
    for node in topology["nodes"]:
        lines.append(f'    "{node}";')
    for edge in topology["edges"]:
        label = f'{edge["source_interface"]} -- {edge["target_interface"]}'
        lines.append(f'    "{edge["source"]}" -- "{edge["target"]}" [label="{label}"];')
    lines.append("}")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    logger.info("DOT file written: %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover network topology via CDP or LLDP using Nornir"
    )
    parser.add_argument("--hosts", required=True, help="Comma-separated seed device IPs/hostnames")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)")
    parser.add_argument("--protocol", choices=["cdp", "lldp"], default="cdp")
    parser.add_argument("--output", default="topology.json", help="JSON output path")
    parser.add_argument("--dot", help="Optional Graphviz DOT output path")
    parser.add_argument("--workers", type=int, default=10, help="Parallel workers (default: 10)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]
    nr = build_nornir(host_list, args.username, args.password, args.platform, args.workers)

    task_fn = collect_cdp if args.protocol == "cdp" else collect_lldp
    logger.info("Collecting %s neighbors from %d host(s)", args.protocol.upper(), len(host_list))

    agg = nr.run(task=task_fn)

    neighbor_data: dict[str, list[dict]] = {}
    failed: list[str] = []
    for host, multi in agg.items():
        if multi.failed:
            logger.warning("Failed on %s: %s", host, multi[0].exception)
            failed.append(host)
            continue
        neighbor_data[host] = normalize(multi[0].result, args.protocol)

    if failed:
        logger.warning("Unreachable hosts (%d): %s", len(failed), ", ".join(failed))

    if not neighbor_data:
        logger.error("No neighbor data collected; check connectivity and credentials")
        sys.exit(1)

    topology = build_topology(neighbor_data)

    with open(args.output, "w") as fh:
        json.dump(topology, fh, indent=2)
    logger.info(
        "Topology saved to %s — %d nodes, %d edges",
        args.output,
        len(topology["nodes"]),
        len(topology["edges"]),
    )

    if args.dot:
        write_dot(topology, args.dot)


if __name__ == "__main__":
    main()
```