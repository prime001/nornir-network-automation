```python
"""
Device Health Monitor - Nornir Network Automation

Purpose:
    Monitor and report on the health status of network devices including
    connectivity, uptime, interface states, and resource utilization.

Usage:
    python device_health_monitor.py --inventory inventory.yml
    python device_health_monitor.py --inventory inventory.yml --groups routers
    python device_health_monitor.py --inventory inventory.yml --output-file health.json --format json

Prerequisites:
    - Nornir with netmiko plugin installed
    - Network device inventory in YAML format
    - Devices must be reachable via SSH with valid credentials
    - Supported device types: Cisco IOS, Junos, Arista EOS
"""

import argparse
import json
import logging
from typing import Dict, Any
from datetime import datetime
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_device_uptime(task: Task) -> Result:
    """Retrieve device uptime using show version command."""
    try:
        if task.host.platform == "cisco_ios":
            cmd = "show version"
        elif task.host.platform == "juniper_junos":
            cmd = "show system uptime"
        else:
            cmd = "show version"

        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result

        uptime = "Unknown"
        for line in output.split("\n"):
            if "uptime" in line.lower():
                uptime = line.strip()
                break

        return Result(host=task.host, result={"uptime": uptime})

    except Exception as e:
        logger.error(f"{task.host.name}: Failed to get uptime - {str(e)}")
        return Result(host=task.host, result={"uptime": "Error", "error": str(e)})


def get_interface_summary(task: Task) -> Result:
    """Summarize interface status (up/down counts)."""
    try:
        result = task.run(netmiko_send_command, command_string="show interfaces")
        output = result[0].result

        up_count = 0
        down_count = 0

        for line in output.split("\n"):
            line_lower = line.lower()
            if " is up" in line_lower and "admin" not in line_lower:
                up_count += 1
            elif " is down" in line_lower:
                down_count += 1

        return Result(
            host=task.host,
            result={"interfaces_up": up_count, "interfaces_down": down_count}
        )

    except Exception as e:
        logger.error(f"{task.host.name}: Failed to get interfaces - {str(e)}")
        return Result(
            host=task.host,
            result={"interfaces_up": 0, "interfaces_down": 0, "error": str(e)}
        )


def get_system_resources(task: Task) -> Result:
    """Retrieve CPU and memory utilization."""
    resources = {"cpu_usage": "N/A", "memory_usage": "N/A"}

    try:
        if task.host.platform == "cisco_ios":
            result = task.run(netmiko_send_command, command_string="show processes cpu")
            output = result[0].result

            for line in output.split("\n"):
                if "CPU utilization" in line or "5 sec:" in line:
                    resources["cpu_usage"] = line.strip()
                    break

    except Exception as e:
        logger.warning(f"{task.host.name}: Could not retrieve resources - {str(e)}")

    return Result(host=task.host, result=resources)


def monitor_device_health(task: Task) -> Result:
    """Aggregate health metrics for a device."""
    health = {
        "device_name": task.host.name,
        "platform": task.host.platform,
        "timestamp": datetime.now().isoformat(),
        "status": "HEALTHY"
    }

    try:
        uptime_result = task.run(get_device_uptime)
        health["uptime"] = uptime_result[0].result.get("uptime", "Unknown")

        interface_result = task.run(get_interface_summary)
        health["interfaces_up"] = interface_result[0].result.get("interfaces_up", 0)
        health["interfaces_down"] = interface_result[0].result.get("interfaces_down", 0)

        resource_result = task.run(get_system_resources)
        health["cpu_usage"] = resource_result[0].result.get("cpu_usage", "N/A")

        if health["interfaces_down"] > 0:
            health["status"] = "WARNING"

    except Exception as e:
        logger.error(f"{task.host.name}: Health check failed - {str(e)}")
        health["status"] = "FAILED"
        health["error"] = str(e)

    return Result(host=task.host, result=health)


def main():
    parser = argparse.ArgumentParser(
        description="Monitor network device health and status"
    )
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to Nornir inventory file (YAML)"
    )
    parser.add_argument(
        "--groups",
        help="Comma-separated list of device groups to monitor"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--output-file",
        help="Write results to file (optional)"
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} devices")
    except Exception as e:
        logger.error(f"Failed to load inventory: {str(e)}")
        return 1

    if args.groups:
        group_list = [g.strip() for g in args.groups.split(",")]
        nr = nr.filter(groups__contains=group_list)
        logger.info(f"Filtered to {len(nr.inventory.hosts)} devices")

    results = nr.run(task=monitor_device_health)

    health_report = {}
    for host_name, multi_result in results.items():
        if multi_result.failed:
            health_report[host_name] = {"status": "UNREACHABLE"}
        else:
            health_report[host_name] = multi_result[0].result

    if args.format == "json":
        output = json.dumps(health_report, indent=2)
    else:
        output = "\n" + "=" * 70 + "\n"
        output += "DEVICE HEALTH REPORT\n"
        output += "=" * 70 + "\n"

        for device_name, health_data in health_report.items():
            status_symbol = "✓" if health_data["status"] == "HEALTHY" else "✗"
            output += f"\n{status_symbol} {device_name} ({health_data.get('platform', 'Unknown')})\n"
            output += "-" * 70 + "\n"
            output += f"  Status:            {health_data.get('status', 'Unknown')}\n"
            output += f"  Uptime:            {health_data.get('uptime', 'Unknown')}\n"
            output += f"  Interfaces Up:     {health_data.get('interfaces_up', 0)}\n"
            output += f"  Interfaces Down:   {health_data.get('interfaces_down', 0)}\n"
            output += f"  CPU Usage:         {health_data.get('cpu_usage', 'N/A')}\n"

            if "error" in health_data:
                output += f"  Error:             {health_data['error']}\n"

    if args.output_file:
        with open(args.output_file, "w") as f:
            f.write(output)
        logger.info(f"Report written to {args.output_file}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    exit(main())
```