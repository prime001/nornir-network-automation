The write was blocked — outputting the script content directly as requested:

"""
device_health.py — Parallel device health and resource utilization monitor.

Collects CPU load, memory utilization, uptime, and hardware environment status
(fans, temperature, power supplies) from network devices using NAPALM via
Nornir's threaded runner. Raises configurable alerts when thresholds are exceeded.

Usage:
    python device_health.py --inventory hosts.yaml
    python device_health.py --inventory hosts.yaml --filter-group core_routers
    python device_health.py --inventory hosts.yaml --threshold-cpu 80 --threshold-mem 85
    python device_health.py --inventory hosts.yaml --alerts-only --workers 20

Prerequisites:
    pip install nornir nornir-napalm nornir-utils napalm
    Nornir inventory files (hosts.yaml, groups.yaml, defaults.yaml) with
    platform set to a NAPALM-supported driver (ios, eos, junos, nxos_ssh, etc.)
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class DeviceHealth:
    hostname: str
    vendor: str = ""
    model: str = ""
    os_version: str = ""
    uptime_hours: float = 0.0
    cpu_load: float = 0.0
    memory_used_pct: float = 0.0
    fans_ok: bool = True
    temperature_ok: bool = True
    psu_ok: bool = True
    alerts: List[str] = field(default_factory=list)


def collect_health(task: Task, cpu_threshold: float, mem_threshold: float) -> Result:
    result = task.run(task=napalm_get, getters=["facts", "environment"])
    facts = result[0].result.get("facts", {})
    env = result[0].result.get("environment", {})

    health = DeviceHealth(hostname=task.host.name)
    health.vendor = facts.get("vendor", "unknown")
    health.model = facts.get("model", "unknown")
    health.os_version = facts.get("os_version", "unknown")
    uptime_secs = facts.get("uptime", 0)
    health.uptime_hours = round(uptime_secs / 3600, 1) if uptime_secs else 0.0

    cpu_info = env.get("cpu", {})
    if cpu_info:
        loads = [v.get("%usage", 0) for v in cpu_info.values() if isinstance(v, dict)]
        health.cpu_load = max(loads) if loads else 0.0

    mem_info = env.get("memory", {})
    if mem_info:
        used = mem_info.get("used_ram", 0)
        avail = mem_info.get("available_ram", 0)
        total = used + avail
        health.memory_used_pct = round((used / total) * 100, 1) if total else 0.0

    fans = env.get("fans", {})
    if fans:
        health.fans_ok = all(v.get("status", True) for v in fans.values())

    temps = env.get("temperature", {})
    if temps:
        health.temperature_ok = all(
            not v.get("is_alert", False) and not v.get("is_critical", False)
            for v in temps.values()
            if isinstance(v, dict)
        )

    psus = env.get("power", {})
    if psus:
        health.psu_ok = all(v.get("status", True) for v in psus.values())

    if health.cpu_load > cpu_threshold:
        health.alerts.append(f"CPU {health.cpu_load:.1f}% exceeds {cpu_threshold}%")
    if health.memory_used_pct > mem_threshold:
        health.alerts.append(f"Memory {health.memory_used_pct:.1f}% exceeds {mem_threshold}%")
    if not health.fans_ok:
        health.alerts.append("Fan failure detected")
    if not health.temperature_ok:
        health.alerts.append("Temperature alert or critical")
    if not health.psu_ok:
        health.alerts.append("PSU failure detected")

    return Result(host=task.host, result=health)


def print_health_table(results: Dict[str, DeviceHealth]) -> None:
    col = f"{'Device':<22} {'Vendor':<10} {'Model':<18} {'Uptime(h)':<11} {'CPU%':<7} {'Mem%':<7} Status"
    divider = "-" * len(col)
    print(f"\n{divider}\n{col}\n{divider}")

    alert_lines: List[str] = []
    for hostname, h in sorted(results.items()):
        status = "OK" if not h.alerts else f"ALERT x{len(h.alerts)}"
        print(
            f"{hostname:<22} {h.vendor:<10} {h.model:<18} "
            f"{h.uptime_hours:<11} {h.cpu_load:<7.1f} {h.memory_used_pct:<7.1f} {status}"
        )
        for alert in h.alerts:
            alert_lines.append(f"  [{hostname}] {alert}")

    print(divider)
    if alert_lines:
        print("\nAlerts:")
        print("\n".join(alert_lines))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect device health metrics across a Nornir inventory via NAPALM"
    )
    p.add_argument("--inventory", default="hosts.yaml", help="Hosts inventory file")
    p.add_argument("--groups-file", default="groups.yaml", help="Groups file")
    p.add_argument("--defaults-file", default="defaults.yaml", help="Defaults file")
    p.add_argument("--filter-group", help="Restrict to devices in this Nornir group")
    p.add_argument("--filter-host", help="Run against a single host by name")
    p.add_argument("--username", help="Override inventory username")
    p.add_argument("--password", help="Override inventory password")
    p.add_argument("--workers", type=int, default=10, help="Concurrent threads (default: 10)")
    p.add_argument("--threshold-cpu", type=float, default=75.0,
                   help="CPU%% alert threshold (default: 75)")
    p.add_argument("--threshold-mem", type=float, default=80.0,
                   help="Memory%% alert threshold (default: 80)")
    p.add_argument("--alerts-only", action="store_true",
                   help="Print only devices with active alerts")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": args.inventory,
                "group_file": args.groups_file,
                "defaults_file": args.defaults_file,
            },
        },
    )

    if args.username:
        nr.inventory.defaults.username = args.username
    if args.password:
        nr.inventory.defaults.password = args.password

    if args.filter_group:
        nr = nr.filter(F(groups__contains=args.filter_group))
    if args.filter_host:
        nr = nr.filter(name=args.filter_host)

    if not nr.inventory.hosts:
        print("No hosts matched the given filters.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Collecting health data from {len(nr.inventory.hosts)} device(s) "
        f"with {args.workers} worker(s)..."
    )

    results = nr.run(
        task=collect_health,
        cpu_threshold=args.threshold_cpu,
        mem_threshold=args.threshold_mem,
    )

    health_data: Dict[str, DeviceHealth] = {}
    failed: List[str] = []
    for hostname, multi_result in results.items():
        if multi_result.failed:
            failed.append(hostname)
            logger.error("Failed to collect from %s: %s", hostname, multi_result[0].exception)
        else:
            health_data[hostname] = multi_result[0].result

    if args.alerts_only:
        health_data = {h: d for h, d in health_data.items() if d.alerts}

    if health_data:
        print_health_table(health_data)

    if failed:
        print(f"\nFailed ({len(failed)}): {', '.join(failed)}", file=sys.stderr)

    alert_count = sum(1 for d in health_data.values() if d.alerts)
    print(f"\nSummary: {len(health_data)} collected, {alert_count} with alerts, {len(failed)} failed")

    sys.exit(1 if failed or alert_count else 0)