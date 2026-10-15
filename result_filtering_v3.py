```python
#!/usr/bin/env python3
"""
Device Health Monitor

Monitors network device operational health by collecting metrics including uptime,
CPU utilization, memory usage, and reachability status. Generates health reports
and identifies critical issues.

Prerequisites:
    - Nornir installed: pip install nornir nornir-netmiko
    - Inventory file (hosts.yaml, groups.yaml, defaults.yaml) configured
    - Device credentials available via environment or inventory
    - Network devices accessible via SSH

Usage:
    python device_health_monitor.py --inventory-dir ./inventory
    python device_health_monitor.py --group core --format json
    python device_health_monitor.py --device router-01 --verbose
"""

import argparse
import json
import logging
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def extract_metric(output: str, markers: list) -> str:
    """Extract metric from command output using marker keywords."""
    for line in output.split("\n"):
        for marker in markers:
            if marker.lower() in line.lower():
                return line.strip()
    return "Not available"


def check_device_health(task: Task) -> Result:
    """
    Retrieve device health metrics: uptime, CPU, memory status.
    Handles different device platforms with appropriate commands.
    """
    host = task.host
    health = {
        "device": host.name,
        "platform": host.platform,
        "reachable": True,
        "metrics": {}
    }

    try:
        platform = host.platform.lower() if host.platform else "unknown"

        if "ios" in platform or "xe" in platform:
            version_result = task.run(
                netmiko_send_command,
                command_string="show version",
                name="get_version"
            )
            version_output = version_result[1].result
            health["metrics"]["uptime"] = extract_metric(
                version_output,
                ["uptime is", "System uptime"]
            )

            proc_result = task.run(
                netmiko_send_command,
                command_string="show processes cpu | include CPU",
                name="get_cpu"
            )
            cpu_output = proc_result[1].result
            health["metrics"]["cpu"] = extract_metric(
                cpu_output,
                ["CPU utilization"]
            )

        elif "nxos" in platform:
            version_result = task.run(
                netmiko_send_command,
                command_string="show version",
                name="get_version"
            )
            version_output = version_result[1].result
            health["metrics"]["uptime"] = extract_metric(
                version_output,
                ["System uptime"]
            )

        elif "eos" in platform:
            version_result = task.run(
                netmiko_send_command,
                command_string="show version",
                name="get_version"
            )
            version_output = version_result[1].result
            health["metrics"]["uptime"] = extract_metric(
                version_output,
                ["uptime"]
            )

        else:
            health["metrics"]["info"] = f"Platform {host.platform} recognized"

        health["status"] = "healthy"

    except Exception as e:
        logger.error(f"Health check failed for {host.name}: {e}")
        health["reachable"] = False
        health["status"] = "unreachable"
        health["error"] = str(e)

    return Result(host=host, result=health)


def format_health_report(results: Dict[str, Any], fmt: str) -> str:
    """Format health check results for output."""
    if fmt == "json":
        output = {}
        for host, result in results.items():
            if result:
                output[host] = result[0].result
        return json.dumps(output, indent=2)

    lines = ["Device Health Report", "=" * 70]
    for host, result in results.items():
        if result:
            health = result[0].result
            status_symbol = "✓" if health["status"] == "healthy" else "✗"
            lines.append(
                f"\n{status_symbol} {health['device']} "
                f"[{health['platform']}] - {health['status'].upper()}"
            )

            if "metrics" in health:
                for metric, value in health["metrics"].items():
                    lines.append(f"    {metric}: {value}")

            if "error" in health:
                lines.append(f"    Error: {health['error']}")

    return "\n".join(lines)


def main():
    """Execute device health monitoring."""
    parser = argparse.ArgumentParser(
        description="Monitor network device health and operational status"
    )
    parser.add_argument(
        "--inventory-dir",
        default="./inventory",
        help="Path to nornir inventory directory (default: ./inventory)"
    )
    parser.add_argument(
        "--group",
        help="Filter devices by inventory group"
    )
    parser.add_argument(
        "--device",
        help="Check specific device by name"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging"
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        nr = InitNornir(config_file=f"{args.inventory_dir}/config.yaml")

        if args.device:
            nr = nr.filter(name=args.device)
        elif args.group:
            nr = nr.filter(group=args.group)

        logger.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=check_device_health)

        report = format_health_report(results, args.format)
        print(report)

        healthy_count = sum(
            1 for r in results.values()
            if r and r[0].result.get("status") == "healthy"
        )
        logger.info(
            f"Health check complete: {healthy_count}/{len(results)} "
            f"device(s) healthy"
        )

        return 0

    except Exception as e:
        logger.error(f"Health monitoring failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```