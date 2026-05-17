Device Health Monitor — nornir-network-automation
==================================================
Collects CPU utilization, memory usage, uptime, and environmental status
(temperature, fans, power) from network devices in parallel using NAPALM
via Nornir.

Usage:
    python device_health.py --hosts 192.168.1.1,192.168.1.2 \\
        --username admin --password secret \\
        --platform ios --workers 10 --warn-cpu 70 --warn-mem 80

Prerequisites:
    pip install nornir nornir-napalm napalm

Output:
    Console table + optional JSON file (--output health_report.json)
"""

import argparse
import json
import logging
import sys
from datetime import timedelta
from typing import Any

from nornir import InitNornir
from nornir.core.inventory import (
    Defaults,
    Group,
    Groups,
    Host,
    Hosts,
    Inventory,
)
from nornir.core.task import MultiResult, Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("device_health")


def build_inventory(hosts: list[str], username: str, password: str, platform: str) -> Inventory:
    host_objects = {}
    for addr in hosts:
        name = addr.strip()
        host_objects[name] = Host(
            name=name,
            hostname=name,
            username=username,
            password=password,
            platform=platform,
            groups=[],
        )
    return Inventory(hosts=Hosts(host_objects), groups=Groups({}), defaults=Defaults())


def collect_health(task: Task) -> Result:
    facts_result = task.run(task=napalm_get, getters=["get_facts", "get_environment"])
    facts = facts_result[0].result.get("get_facts", {})
    env = facts_result[0].result.get("get_environment", {})

    cpu_values = [v.get("%usage", 0.0) for v in env.get("cpu", {}).values()]
    cpu_avg = sum(cpu_values) / len(cpu_values) if cpu_values else None

    mem = env.get("memory", {})
    mem_used = mem.get("used_ram", None)
    mem_avail = mem.get("available_ram", None)
    mem_pct = None
    if mem_used is not None and mem_avail is not None and (mem_used + mem_avail) > 0:
        mem_pct = (mem_used / (mem_used + mem_avail)) * 100

    temps = env.get("temperature", {})
    temp_alerts = [
        sensor for sensor, data in temps.items()
        if data.get("is_alert") or data.get("is_critical")
    ]

    fans_ok = all(
        f.get("status") for f in env.get("fans", {}).values()
    )
    power_ok = all(
        p.get("status") for p in env.get("power", {}).values()
    )

    uptime_sec = facts.get("uptime", None)
    uptime_str = str(timedelta(seconds=int(uptime_sec))) if uptime_sec is not None else "N/A"

    return Result(
        host=task.host,
        result={
            "hostname": facts.get("hostname", task.host.name),
            "model": facts.get("model", "unknown"),
            "os_version": facts.get("os_version", "unknown"),
            "uptime": uptime_str,
            "cpu_pct": round(cpu_avg, 1) if cpu_avg is not None else None,
            "mem_pct": round(mem_pct, 1) if mem_pct is not None else None,
            "temp_alerts": temp_alerts,
            "fans_ok": fans_ok,
            "power_ok": power_ok,
        },
    )


def print_report(results: MultiResult, warn_cpu: float, warn_mem: float) -> list[dict[str, Any]]:
    header = f"{'Host':<20} {'Model':<16} {'Uptime':<14} {'CPU%':>5} {'Mem%':>5} {'Fans':>5} {'Pwr':>5} {'Temp Alerts'}"
    print(header)
    print("-" * len(header))

    report = []
    for host, multi in results.items():
        if multi.failed:
            print(f"{host:<20} ERROR: {multi[0].exception}")
            report.append({"host": host, "error": str(multi[0].exception)})
            continue

        d = multi[0].result
        cpu_str = f"{d['cpu_pct']:.1f}" if d["cpu_pct"] is not None else "N/A"
        mem_str = f"{d['mem_pct']:.1f}" if d["mem_pct"] is not None else "N/A"

        cpu_warn = d["cpu_pct"] is not None and d["cpu_pct"] >= warn_cpu
        mem_warn = d["mem_pct"] is not None and d["mem_pct"] >= warn_mem
        temp_warn = bool(d["temp_alerts"])

        flags = []
        if cpu_warn:
            flags.append("HIGH_CPU")
        if mem_warn:
            flags.append("HIGH_MEM")
        if temp_warn:
            flags.append(f"TEMP:{','.join(d['temp_alerts'])}")
        if not d["fans_ok"]:
            flags.append("FAN_FAIL")
        if not d["power_ok"]:
            flags.append("PWR_FAIL")

        alert_str = " ".join(flags) if flags else "OK"
        fans_str = "OK" if d["fans_ok"] else "FAIL"
        pwr_str = "OK" if d["power_ok"] else "FAIL"

        print(
            f"{d['hostname']:<20} {d['model']:<16} {d['uptime']:<14} "
            f"{cpu_str:>5} {mem_str:>5} {fans_str:>5} {pwr_str:>5} {alert_str}"
        )

        report_entry = dict(d)
        report_entry["host"] = host
        report_entry["alerts"] = flags
        report.append(report_entry)

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect CPU, memory, uptime, and environmental health from network devices."
    )
    parser.add_argument("--hosts", required=True, help="Comma-separated list of device IPs/hostnames")
    parser.add_argument("--username", required=True, help="SSH/NAPALM username")
    parser.add_argument("--password", required=True, help="SSH/NAPALM password")
    parser.add_argument("--platform", default="ios", help="NAPALM platform (ios, eos, junos, nxos_ssh). Default: ios")
    parser.add_argument("--workers", type=int, default=10, help="Parallel worker threads. Default: 10")
    parser.add_argument("--warn-cpu", type=float, default=75.0, help="CPU %% threshold for warning. Default: 75")
    parser.add_argument("--warn-mem", type=float, default=85.0, help="Memory %% threshold for warning. Default: 85")
    parser.add_argument("--output", help="Write JSON report to this file path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    host_list = [h.strip() for h in args.hosts.split(",") if h.strip()]
    if not host_list:
        print("ERROR: no hosts specified", file=sys.stderr)
        sys.exit(1)

    inventory = build_inventory(host_list, args.username, args.password, args.platform)
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={"plugin": "SimpleInventory"},
        logging={"enabled": False},
    )
    nr.inventory = inventory

    logger.info("Collecting health data from %d device(s) with %d workers", len(host_list), args.workers)
    results = nr.run(task=collect_health, name="device_health")

    report = print_report(results, args.warn_cpu, args.warn_mem)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nReport written to {args.output}")

    failed = sum(1 for m in results.values() if m.failed)
    sys.exit(1 if failed else 0)