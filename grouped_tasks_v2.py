```python
"""
device_health.py - Nornir device health snapshot

Collects CPU utilization, memory usage, and system uptime from network devices
concurrently via Netmiko SSH.  Results are printed as a summary table and
optionally exported to JSON.  Devices exceeding configurable thresholds are
flagged as WARN in the output.

Usage:
    python device_health.py [--config-dir DIR] [--groups core access]
                            [--cpu-warn 70] [--mem-warn 80]
                            [--output health.json] [--verbose]

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory files (config.yaml, hosts.yaml, groups.yaml) must be
    present in the working directory or the path supplied via --config-dir.
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logger = logging.getLogger(__name__)


def _parse_cpu(output: str) -> Optional[float]:
    """Extract 1-minute CPU busy percent from show-processes output."""
    patterns = [
        r"CPU utilization[^:]*:\s*(\d+)%\s+one minute",
        r"CPU utilization\s*:\s*(\d+)%",
        r"(\d+)\s*%\s+CPU\s+util",
        r"one minute:\s*(\d+)%",
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _parse_memory(output: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (total_kb, used_kb) parsed from show-version or show-memory."""
    # IOS: "131072K/32768K bytes of memory"  (processor/IO split)
    m = re.search(r"with\s+(\d+)K/(\d+)K\s+bytes of memory", output, re.IGNORECASE)
    if m:
        proc_kb = int(m.group(1))
        io_kb = int(m.group(2))
        total_kb = proc_kb + io_kb
        return total_kb, proc_kb
    # NXOS / IOS-XR: "Memory: total=4096 MB used=1200 MB"
    m = re.search(r"total=(\d+)\s*MB.*?used=(\d+)\s*MB", output, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1024, int(m.group(2)) * 1024
    return None, None


def _parse_uptime(output: str) -> Optional[str]:
    """Extract uptime string from show-version output."""
    m = re.search(r"uptime is (.+?)(?:\r?\n|,(?:\s|$))", output, re.IGNORECASE)
    return m.group(1).strip() if m else None


def collect_health(task: Task, cpu_warn: int, mem_warn: int) -> Result:
    """Grouped task: gather CPU, memory, and uptime for one device."""
    cpu_raw = task.run(
        task=netmiko_send_command,
        command_string="show processes cpu | include CPU utilization",
        name="cpu",
    ).result

    ver_raw = task.run(
        task=netmiko_send_command,
        command_string="show version",
        name="version",
    ).result

    cpu_pct = _parse_cpu(cpu_raw)
    total_kb, used_kb = _parse_memory(ver_raw)
    uptime = _parse_uptime(ver_raw)

    mem_pct: Optional[float] = None
    if total_kb and used_kb:
        mem_pct = round(used_kb / total_kb * 100, 1)

    record = {
        "host": task.host.name,
        "group": list(task.host.groups)[0] if task.host.groups else None,
        "platform": task.host.platform,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime": uptime or "unknown",
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "cpu_warn": cpu_pct is not None and cpu_pct >= cpu_warn,
        "mem_warn": mem_pct is not None and mem_pct >= mem_warn,
    }

    status = "WARN" if (record["cpu_warn"] or record["mem_warn"]) else "OK"
    logger.info(
        "%-20s [%-4s]  cpu=%-5s  mem=%-5s  uptime=%s",
        task.host.name,
        status,
        f"{cpu_pct:.0f}%" if cpu_pct is not None else "?",
        f"{mem_pct:.0f}%" if mem_pct is not None else "?",
        record["uptime"],
    )
    return Result(host=task.host, result=record)


def print_table(records: list) -> None:
    col = {"host": 20, "group": 12, "uptime": 26, "cpu": 7, "mem": 7, "status": 6}
    header = (
        f"{'HOST':<{col['host']}} {'GROUP':<{col['group']}} "
        f"{'UPTIME':<{col['uptime']}} {'CPU%':>{col['cpu']}} "
        f"{'MEM%':>{col['mem']}} STATUS"
    )
    sep = "-" * len(header)
    print(f"\n{sep}\n{header}\n{sep}")
    for r in records:
        status = "WARN" if (r.get("cpu_warn") or r.get("mem_warn")) else "OK"
        cpu = f"{r['cpu_pct']:.0f}" if r.get("cpu_pct") is not None else "?"
        mem = f"{r['mem_pct']:.0f}" if r.get("mem_pct") is not None else "?"
        group = r.get("group") or ""
        print(
            f"{r['host']:<{col['host']}} {group:<{col['group']}} "
            f"{r.get('uptime', 'unknown'):<{col['uptime']}} "
            f"{cpu:>{col['cpu']}} {mem:>{col['mem']}} {status}"
        )
    print(sep + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect CPU, memory, and uptime from network devices via Nornir"
    )
    parser.add_argument(
        "--config-dir", default=".", metavar="DIR",
        help="Directory containing Nornir config.yaml (default: .)"
    )
    parser.add_argument(
        "--groups", nargs="+", metavar="GROUP",
        help="Limit run to hosts in these Nornir groups"
    )
    parser.add_argument(
        "--cpu-warn", type=int, default=70, metavar="PCT",
        help="CPU %% threshold for WARN flag (default: 70)"
    )
    parser.add_argument(
        "--mem-warn", type=int, default=80, metavar="PCT",
        help="Memory %% threshold for WARN flag (default: 80)"
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Write JSON results to this file"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print raw Nornir task output"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        nr = InitNornir(config_file=f"{args.config_dir}/config.yaml")
    except FileNotFoundError:
        logger.error("config.yaml not found in %s", args.config_dir)
        sys.exit(1)

    if args.groups:
        nr = nr.filter(lambda h: any(g in h.groups for g in args.groups))

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check inventory or --groups filter")
        sys.exit(1)

    logger.info("Health check starting on %d host(s)", len(nr.inventory.hosts))

    agg = nr.run(
        task=collect_health,
        cpu_warn=args.cpu_warn,
        mem_warn=args.mem_warn,
        name="device_health",
    )

    if args.verbose:
        print_result(agg)

    records = []
    for host, multi in agg.items():
        if multi.failed:
            logger.warning("FAILED  %s: %s", host, multi.exception)
            records.append({"host": host, "error": str(multi.exception)})
        else:
            records.append(multi[0].result)

    healthy = [r for r in records if "cpu_pct" in r]
    print_table(healthy)

    warn_count = sum(1 for r in healthy if r.get("cpu_warn") or r.get("mem_warn"))
    fail_count = sum(1 for r in records if "error" in r)
    ok_count = len(healthy) - warn_count
    logger.info("Done: %d OK  %d WARN  %d FAILED", ok_count, warn_count, fail_count)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(records, fh, indent=2, default=str)
        logger.info("Results written to %s", args.output)

    sys.exit(1 if fail_count else 0)


if __name__ == "__main__":
    main()
```