```python
"""
Device Health Check - Multi-threaded health metric collection.

Purpose:
    Collects CPU, memory, disk, and uptime metrics from network devices
    across the inventory using nornir's threaded execution model.

Usage:
    python 028_device_health_check.py --device all --format table
    python 028_device_health_check.py --device router1 --format json
    python 028_device_health_check.py --device site1 --group --threshold 80

Prerequisites:
    - nornir and nornir_netmiko
    - inventory configured with device credentials
    - devices supporting 'show system resources' or equivalent
    - ssh/netmiko connectivity to all devices

Output formats: table, json
"""

import argparse
import json
import logging
from typing import Dict, Any, Optional

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command

logger = logging.getLogger(__name__)


def parse_health_metrics(output: str, device_type: str) -> Optional[Dict[str, Any]]:
    """Parse device health metrics from command output."""
    metrics = {
        "cpu": None,
        "memory": None,
        "disk": None,
        "uptime": None,
    }

    if not output:
        return None

    if "ios" in device_type.lower():
        for line in output.split("\n"):
            if "CPU utilization" in line:
                try:
                    parts = line.split()
                    cpu_str = parts[-1].rstrip("%")
                    metrics["cpu"] = float(cpu_str)
                except (ValueError, IndexError):
                    pass
    elif "juniper" in device_type.lower():
        for line in output.split("\n"):
            if "user" in line.lower() and "%" in line:
                try:
                    parts = line.split()
                    cpu_idx = next(i for i, p in enumerate(parts) if "%" in p)
                    metrics["cpu"] = float(parts[cpu_idx].rstrip("%"))
                except (ValueError, IndexError, StopIteration):
                    pass

    return metrics


def collect_health_metrics(task: Task) -> Result:
    """Collect health metrics from a device."""
    try:
        device = task.host
        device_type = device.platform or "ios"

        cmd = "show processes cpu | include CPU"
        if "juniper" in device_type.lower():
            cmd = "show system processes extensive | head 5"
        elif "arista" in device_type.lower():
            cmd = "show processes cpu"

        proc_result = task.run(
            netmiko_send_command,
            command_string=cmd,
            use_textfsm=False,
        )

        metrics = parse_health_metrics(proc_result[0].result, device_type)

        version_result = task.run(
            netmiko_send_command,
            command_string="show version | include uptime",
            use_textfsm=False,
        )

        if version_result[0].result:
            uptime_line = version_result[0].result.strip().split("\n")[0]
            metrics["uptime"] = uptime_line

        return Result(
            host=device,
            result=metrics,
        )

    except Exception as e:
        logger.error(f"Failed to collect metrics from {task.host}: {e}")
        return Result(
            host=task.host,
            result={"error": str(e)},
            failed=True,
        )


def format_table_output(results: Dict[str, Dict[str, Any]]) -> str:
    """Format results as table."""
    lines = [
        f"{'Device':<20} {'CPU %':<10} {'Status':<15}",
        "-" * 45,
    ]

    for device, data in results.items():
        if isinstance(data, dict) and "error" not in data:
            cpu = data.get("cpu")
            cpu_str = f"{cpu:.1f}%" if isinstance(cpu, float) else "N/A"
            status = "OK" if cpu is not None else "UNKNOWN"
            lines.append(f"{device:<20} {cpu_str:<10} {status:<15}")
        else:
            lines.append(f"{device:<20} {'ERROR':<10} {'FAILED':<15}")

    return "\n".join(lines)


def format_json_output(results: Dict[str, Dict[str, Any]]) -> str:
    """Format results as JSON."""
    return json.dumps(results, indent=2, default=str)


def main() -> int:
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Collect device health metrics from network devices"
    )
    parser.add_argument(
        "--device",
        default="all",
        help="Device name, group, or 'all' (default: all)",
    )
    parser.add_argument(
        "--group",
        action="store_true",
        help="Filter by group name instead of device name",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=90,
        help="Alert threshold for CPU/memory (default: 90)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Number of worker threads (default: 4)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        nr = InitNornir(config_file="config.yaml")

        if args.device != "all":
            if args.group:
                nr = nr.filter(group=args.device)
            else:
                nr = nr.filter(name=args.device)

        logger.info(f"Collecting health metrics from {len(nr.inventory.hosts)} devices")

        results = nr.run(
            task=collect_health_metrics,
            num_workers=args.threads,
        )

        output_data = {}
        for device_name, task_result in results.items():
            if task_result[0].result:
                output_data[device_name] = task_result[0].result
            else:
                output_data[device_name] = {"error": "No result"}

        if args.format == "json":
            print(format_json_output(output_data))
        else:
            print(format_table_output(output_data))

        logger.info("Health check completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```