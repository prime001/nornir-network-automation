#!/usr/bin/env python3
"""
Device Health Monitor - Nornir Network Automation Script

Purpose:
    Collects and reports device health metrics (CPU, memory, uptime) across
    network inventory, enabling rapid identification of devices exceeding
    threshold values for proactive capacity planning and troubleshooting.

Usage:
    python device_health_monitor.py --username admin
    python device_health_monitor.py --device core-01 --username admin
    python device_health_monitor.py --cpu-threshold 75 --memory-threshold 80

Prerequisites:
    - Nornir with netmiko transport plugin installed
    - Inventory file (inventory.yaml) with device definitions
    - SSH connectivity to target devices with specified credentials
    - Devices support 'show version' and 'show processes' commands
    - Tested on Cisco IOS/IOSXE/NXOS, adaptable for other vendors

Returns:
    Exit code 0 if all devices healthy, 1 if any threshold exceeded or error.

Author: Network Automation Portfolio
License: MIT
"""

import argparse
import json
import logging
import sys
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_health_metrics(device_name: str, version_output: str, process_output: str) -> Dict:
    """Extract CPU, memory, and uptime from device command output."""
    metrics = {
        "device": device_name,
        "cpu": None,
        "memory": None,
        "uptime": None,
        "error": None,
    }

    if not version_output or not process_output:
        metrics["error"] = "Missing command output"
        return metrics

    for line in version_output.split('\n'):
        if 'uptime' in line.lower():
            metrics["uptime"] = line.strip()
            break

    for line in process_output.split('\n'):
        if 'CPU utilization' in line or 'CPU util' in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if '%' in part:
                    try:
                        cpu_val = float(parts[i - 1])
                        if 0 <= cpu_val <= 100:
                            metrics["cpu"] = cpu_val
                            break
                    except (ValueError, IndexError):
                        pass

        if 'Memory' in line and ('used' in line or 'used:' in line):
            parts = line.split()
            for i, part in enumerate(parts):
                if '%' in part:
                    try:
                        mem_val = float(parts[i - 1])
                        if 0 <= mem_val <= 100:
                            metrics["memory"] = mem_val
                            break
                    except (ValueError, IndexError):
                        pass

    return metrics


def check_device_health(task, cpu_threshold: int, memory_threshold: int):
    """Nornir task: collect device health metrics."""
    try:
        version_result = task.run(
            netmiko_send_command,
            command_string="show version",
            name="show_version",
        )
        version_output = version_result[0].result

        process_result = task.run(
            netmiko_send_command,
            command_string="show processes",
            name="show_processes",
        )
        process_output = process_result[0].result

        metrics = parse_health_metrics(
            task.host.name, version_output, process_output
        )
        metrics["cpu_threshold_exceeded"] = (
            metrics["cpu"] is not None and metrics["cpu"] > cpu_threshold
        )
        metrics["memory_threshold_exceeded"] = (
            metrics["memory"] is not None and metrics["memory"] > memory_threshold
        )

        return metrics

    except Exception as exc:
        logger.error(f"Error on {task.host.name}: {exc}")
        return {
            "device": task.host.name,
            "error": str(exc),
            "cpu": None,
            "memory": None,
            "uptime": None,
        }


def filter_unhealthy_devices(results: Dict, cpu_th: int, mem_th: int) -> List[Dict]:
    """Extract devices exceeding thresholds or with errors."""
    unhealthy = []
    for host_name, task_result in results.items():
        if not isinstance(task_result, list) or not task_result:
            continue

        metrics = task_result[0].result
        has_error = metrics.get("error") is not None
        exceeds_cpu = metrics.get("cpu_threshold_exceeded", False)
        exceeds_mem = metrics.get("memory_threshold_exceeded", False)

        if has_error or exceeds_cpu or exceeds_mem:
            unhealthy.append(metrics)

    return sorted(unhealthy, key=lambda x: x.get("cpu", 0), reverse=True)


def print_report(devices: List[Dict], output_format: str):
    """Display health report."""
    if output_format == "json":
        print(json.dumps(devices, indent=2, default=str))
        return

    print("\n" + "=" * 75)
    print("DEVICE HEALTH REPORT - THRESHOLD VIOLATIONS")
    print("=" * 75 + "\n")

    if not devices:
        print("✓ All devices healthy - no thresholds exceeded.\n")
        return

    for device in devices:
        print(f"Device: {device.get('device')}")
        if device.get("error"):
            print(f"  [ERROR] {device['error']}")
        else:
            cpu_str = f"{device.get('cpu')}%" if device.get("cpu") is not None else "N/A"
            mem_str = f"{device.get('memory')}%" if device.get("memory") is not None else "N/A"
            print(f"  CPU:    {cpu_str}")
            print(f"  Memory: {mem_str}")
            if device.get("uptime"):
                print(f"  Uptime: {device['uptime']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Monitor device health metrics across network inventory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "--device", help="Filter to specific device by hostname"
    )
    parser.add_argument(
        "--group", help="Filter to devices in specific group"
    )
    parser.add_argument(
        "--username", required=True, help="SSH username for device access"
    )
    parser.add_argument(
        "--password", help="SSH password (or set NORNIR_PASSWORD environment variable)"
    )
    parser.add_argument(
        "--cpu-threshold",
        type=int,
        default=75,
        help="CPU utilization alert threshold %% (default: 75)",
    )
    parser.add_argument(
        "--memory-threshold",
        type=int,
        default=80,
        help="Memory utilization alert threshold %% (default: 80)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Inventory loaded: {len(nr.inventory.hosts)} hosts")
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Failed to load inventory: {exc}")
        sys.exit(1)

    if args.device:
        nr = nr.filter(name=args.device)
    if args.group:
        nr = nr.filter(F(groups__contains=args.group))

    if not nr.inventory.hosts:
        logger.warning("No hosts match filter criteria")
        sys.exit(0)

    logger.info(f"Starting health check on {len(nr.inventory.hosts)} device(s)")

    results = nr.run(
        task=check_device_health,
        cpu_threshold=args.cpu_threshold,
        memory_threshold=args.memory_threshold,
    )

    unhealthy = filter_unhealthy_devices(
        results, args.cpu_threshold, args.memory_threshold
    )
    print_report(unhealthy, args.output)

    logger.info(f"Health check complete: {len(unhealthy)} device(s) with issues")
    sys.exit(0 if not unhealthy else 1)


if __name__ == "__main__":
    main()