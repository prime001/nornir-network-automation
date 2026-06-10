```python
"""
Device Health Monitor

Gathers and reports health metrics from network devices using Nornir.

Purpose:
    Monitor device connectivity, reachability, and basic health across the
    network inventory. Collects device information and validates SSH/Netmiko
    connectivity. Useful for identifying unreachable devices and basic health
    status before running operational tasks.

Usage:
    python device_health_monitor.py --devices all
    python device_health_monitor.py --devices rtr-core-01,rtr-core-02
    python device_health_monitor.py --devices all --format json
    python device_health_monitor.py --devices all --verbose

Prerequisites:
    - Nornir installed with netmiko connection plugin
    - config.yaml with inventory settings
    - hosts.yaml with device definitions
    - SSH credentials configured (username/password in group_vars or env)
    - Network connectivity to target devices
"""

import argparse
import json
import logging
import sys
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_device_health(task: Task) -> Result:
    """Check device connectivity and gather basic health information."""
    health_data = {
        "device": task.host.name,
        "hostname": task.host.hostname,
        "platform": task.host.get("platform", "unknown"),
        "status": "unknown",
    }

    try:
        cmd = task.host.get("health_check_cmd", "show version")
        output = task.host.send_command(cmd)

        if output and len(output) > 0:
            health_data["status"] = "healthy"
            health_data["connected"] = True
            health_data["response_length"] = len(output)

            if "uptime" in output.lower():
                health_data["uptime_available"] = True
        else:
            health_data["status"] = "degraded"
            health_data["connected"] = True
            health_data["response_length"] = 0

        return Result(host=task.host, result=health_data)

    except Exception as e:
        health_data["status"] = "unreachable"
        health_data["connected"] = False
        health_data["error"] = str(e)
        logger.warning(f"Device {task.host.name} unreachable: {e}")
        return Result(host=task.host, result=health_data, failed=True)


def validate_device_config(task: Task) -> Result:
    """Validate device has required attributes for operation."""
    validation = {
        "device": task.host.name,
        "valid": True,
        "issues": [],
    }

    if not task.host.hostname:
        validation["valid"] = False
        validation["issues"].append("Missing hostname")

    if not task.host.get("platform"):
        validation["valid"] = False
        validation["issues"].append("Missing platform definition")

    return Result(host=task.host, result=validation)


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Check health and connectivity of network devices",
    )
    parser.add_argument(
        "--devices",
        type=str,
        default="all",
        help="Device(s) to check: 'all' or comma-separated names",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text or json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Nornir config file path",
    )

    return parser.parse_args()


def format_text_report(results: Dict[str, Any]) -> str:
    """Format results as text report."""
    lines = [
        "\n" + "=" * 80,
        "DEVICE HEALTH CHECK REPORT",
        "=" * 80,
        f"{'Device':<25} {'Platform':<15} {'Status':<12} {'Error':<30}",
        "-" * 80,
    ]

    for device_name in sorted(results.keys()):
        for task_result in results[device_name]:
            if task_result.result:
                device = task_result.result.get("device", device_name)
                platform = task_result.result.get("platform", "N/A")
                status = task_result.result.get("status", "unknown")
                error = task_result.result.get("error", "")

                lines.append(
                    f"{device:<25} {platform:<15} {status:<12} {error:<30}",
                )

    lines.append("=" * 80 + "\n")
    return "\n".join(lines)


def format_json_report(results: Dict[str, Any]) -> str:
    """Format results as JSON."""
    output = {}

    for device_name in results.keys():
        for task_result in results[device_name]:
            if task_result.result:
                output[device_name] = task_result.result

    return json.dumps(output, indent=2)


def main():
    """Main execution function."""
    args = parse_arguments()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    try:
        logger.info(f"Loading Nornir configuration from {args.config}")
        nr = InitNornir(config_file=args.config)

    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {args.config}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        sys.exit(1)

    try:
        if args.devices != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            logger.info(f"Filtering to devices: {device_list}")
            nr = nr.filter(F(name__any=device_list))

        if len(nr.inventory.hosts) == 0:
            logger.error("No devices matched the filter")
            sys.exit(1)

        logger.info(f"Starting health check on {len(nr.inventory.hosts)} devices")

        results = nr.run(task=check_device_health)

        healthy = sum(
            1
            for device_results in results.values()
            for task_result in device_results
            if task_result.result and task_result.result.get("status") == "healthy"
        )
        unreachable = sum(
            1
            for device_results in results.values()
            for task_result in device_results
            if task_result.result and task_result.result.get("status") == "unreachable"
        )

        if args.format == "json":
            output = format_json_report(results)
        else:
            output = format_text_report(results)

        print(output)

        logger.info(
            f"Health check complete: {healthy} healthy, {unreachable} unreachable",
        )

        if unreachable > 0:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```