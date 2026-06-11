```python
"""
Device Health Status Report

Purpose: Collect and report system health metrics (CPU, memory, temperature, uptime)
from network devices using Nornir and NAPALM.

Usage:
    python device_health_report.py --inventory inventory.yaml
    python device_health_report.py -i inventory.yaml --device router1
    python device_health_report.py -i inventory.yaml --role core

Prerequisites:
    - Nornir installed: pip install nornir
    - NAPALM drivers: pip install napalm
    - Valid inventory file with device credentials
    - Network devices with NAPALM support (IOS, IOS-XE, JunOS, etc.)
"""

import logging
import argparse
from typing import Any
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_device_health(task, attributes: list) -> None:
    """Retrieve device health metrics using NAPALM."""
    try:
        result = task.run(napalm_get, getters=attributes)
        task.host["health_data"] = result[0].result
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to retrieve data - {e}")
        task.host["health_data"] = None


def filter_devices(nr: Any, device: str = None, role: str = None) -> Any:
    """Filter devices based on name or group/role."""
    if device:
        return nr.filter(name=device)
    if role:
        return nr.filter(F(groups__contains=role))
    return nr


def format_uptime(seconds: float) -> str:
    """Convert seconds to human-readable uptime format."""
    if not seconds:
        return "N/A"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


def get_health_status(cpu: float, memory: float) -> str:
    """Determine health status based on utilization thresholds."""
    if cpu > 95 or memory > 95:
        return "CRITICAL"
    if cpu > 80 or memory > 85:
        return "WARNING"
    return "HEALTHY"


def display_health_report(nr: Any) -> None:
    """Display formatted health report for all devices."""
    print("\n" + "=" * 90)
    print(f"{'Device':<20} {'Uptime':<18} {'CPU %':<10} {'Memory %':<10} {'Status':<12}")
    print("=" * 90)

    for host_name in sorted(nr.inventory.hosts.keys()):
        host = nr.inventory.hosts[host_name]
        health = host.get("health_data")

        if health is None:
            print(f"{host_name:<20} {'UNREACHABLE':<18} {'-':<10} {'-':<10} {'FAILED':<12}")
            continue

        uptime_sec = health.get("uptime", {}).get("uptime", 0)
        cpu_util = health.get("cpu", {}).get("cpu_utilization", 0)
        mem_util = health.get("memory", {}).get("memory_used", 0)

        uptime_str = format_uptime(uptime_sec)
        cpu_str = f"{cpu_util:.1f}"
        mem_str = f"{mem_util:.1f}"
        status = get_health_status(cpu_util, mem_util)

        print(f"{host_name:<20} {uptime_str:<18} {cpu_str:<10} {mem_str:<10} {status:<12}")

    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Collect and display system health metrics from network devices"
    )
    parser.add_argument(
        "-i", "--inventory",
        required=True,
        help="Path to Nornir inventory configuration file"
    )
    parser.add_argument(
        "-d", "--device",
        help="Filter by specific device name"
    )
    parser.add_argument(
        "-r", "--role",
        help="Filter by device role/group"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        logger.info(f"Initializing Nornir from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)

        filtered_nr = filter_devices(nr, args.device, args.role)

        if len(filtered_nr.inventory.hosts) == 0:
            logger.warning("No devices matched the specified filter criteria")
            return

        device_count = len(filtered_nr.inventory.hosts)
        logger.info(f"Collecting health metrics from {device_count} device(s)")

        health_attributes = ["uptime", "cpu", "memory"]
        filtered_nr.run(task=get_device_health, attributes=health_attributes)

        display_health_report(filtered_nr)
        logger.info("Health report completed successfully")

    except Exception as e:
        logger.error(f"Failed to generate report: {e}", exc_info=args.verbose)
        raise


if __name__ == "__main__":
    main()
```