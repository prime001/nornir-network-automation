#!/usr/bin/env python
"""
Device Health Monitor - Collect and display device resource metrics.

Gathers uptime, memory, and CPU utilization from network devices using nornir
and Netmiko. Useful for capacity planning and proactive troubleshooting.

Supports Cisco IOS/IOS-XE devices via SSH.

Prerequisites:
    - nornir and netmiko installed
    - inventory.yaml configured with device credentials
    - SSH access to target devices

Usage:
    python 010_device_health_monitor.py --host router1
    python 010_device_health_monitor.py --group core --log-level DEBUG
    python 010_device_health_monitor.py --inventory custom_inventory.yaml
"""

import logging
import argparse
import re
from pathlib import Path
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)


def parse_device_metrics(version_output: str, memory_output: str) -> dict:
    """Parse show version and show memory statistics output."""
    metrics = {}

    uptime_match = re.search(
        r"uptime is (.+?)(?:\n|$)",
        version_output,
        re.IGNORECASE
    )
    if uptime_match:
        metrics["uptime"] = uptime_match.group(1).strip()

    version_match = re.search(
        r"Cisco IOS Software, (.+?),",
        version_output
    )
    if version_match:
        metrics["version"] = version_match.group(1).strip()

    mem_match = re.search(
        r"Free memory:\s+(\d+)\s+bytes.*?Total memory:\s+(\d+)",
        memory_output,
        re.DOTALL
    )
    if mem_match:
        free = int(mem_match.group(1))
        total = int(mem_match.group(2))
        used_pct = round((total - free) / total * 100, 1)
        metrics["memory_used"] = f"{used_pct}%"

    cpu_match = re.search(
        r"CPU utilization for five seconds.*?:\s+(\d+)%",
        memory_output,
        re.DOTALL | re.IGNORECASE
    )
    if cpu_match:
        metrics["cpu_5sec"] = f"{cpu_match.group(1)}%"

    return metrics if metrics else {"status": "No metrics parsed"}


def gather_device_health(task: Task) -> Result:
    """Collect health metrics from device via show commands."""
    try:
        version_result = task.run(
            netmiko_send_command,
            command_string="show version",
            name="show_version"
        )

        memory_result = task.run(
            netmiko_send_command,
            command_string="show memory statistics",
            name="show_memory"
        )

        metrics = parse_device_metrics(
            version_result[0].result,
            memory_result[1].result
        )

        return Result(host=task.host, result=metrics)

    except Exception as e:
        logger.error(f"{task.host.name}: Collection failed - {str(e)}")
        return Result(
            host=task.host,
            result={"error": str(e)},
            failed=True
        )


def format_results(results) -> None:
    """Display health metrics in formatted table."""
    print("\n" + "=" * 70)
    print(f"{'Device':<20} {'Uptime':<30} {'Memory':<15}")
    print("=" * 70)

    for host_name, task_results in results.items():
        result_data = task_results[0].result

        if "error" in result_data:
            print(f"{host_name:<20} {'ERROR':<30} {result_data['error']:<15}")
        else:
            uptime = result_data.get("uptime", "N/A")[:28]
            memory = result_data.get("memory_used", "N/A")
            print(f"{host_name:<20} {uptime:<30} {memory:<15}")

    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor network device health and resource usage"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to nornir inventory file"
    )
    parser.add_argument(
        "--host",
        help="Monitor specific device by name"
    )
    parser.add_argument(
        "--group",
        help="Monitor devices in a specific group"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if not Path(args.inventory).exists():
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.host:
            nr = nr.filter(name=args.host)
        elif args.group:
            nr = nr.filter(group=args.group)

        if not nr.inventory.hosts:
            logger.error("No devices selected in inventory")
            return 1

        logger.info(f"Monitoring {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=gather_device_health)

        format_results(results)

        failed_count = sum(1 for r in results.values() if r[0].failed)
        if failed_count > 0:
            logger.warning(f"{failed_count} device(s) failed")
            return 1

        return 0

    except Exception as e:
        logger.error(f"Device health monitor failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())