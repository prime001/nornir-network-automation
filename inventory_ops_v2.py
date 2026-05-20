```python
"""
Device Uptime and System Health Reporter

Gathers device uptime and key system metrics from network devices, identifies
recently rebooted or high-uptime devices, and generates a comprehensive health
report for capacity planning and maintenance scheduling.

Prerequisites:
  - Nornir inventory configured with device credentials (config.yaml)
  - Devices must support 'show version' (IOS, IOS-XE, NX-OS) or equivalent
  - Netmiko connector with valid SSH/telnet credentials
  - Devices accessible from execution host

Usage:
  python device_uptime_reporter.py
  python device_uptime_reporter.py --group core --format json
  python device_uptime_reporter.py --threshold 30 --sort uptime
  python device_uptime_reporter.py --log-level DEBUG
"""

import logging
import argparse
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result

logger = logging.getLogger(__name__)


def parse_uptime_string(uptime_text: str) -> Optional[float]:
    """
    Extract uptime days from device output.
    Handles Cisco IOS/NX-OS format: "X days, Y hours, Z minutes"
    """
    try:
        import re
        match = re.search(r'(\d+)\s+day', uptime_text)
        if match:
            days = int(match.group(1))
            hours_match = re.search(r'(\d+)\s+hour', uptime_text)
            hours = int(hours_match.group(1)) if hours_match else 0
            return days + (hours / 24)
        return None
    except (ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse uptime: {e}")
        return None


def gather_device_uptime(task: Task) -> Result:
    """
    Execute 'show version' on device and extract uptime information.
    Returns structured dict with device health metrics.
    """
    try:
        result = task.run(
            netmiko_send_command,
            command_string="show version",
            use_textfsm=False
        )
        output = result[0].result
        uptime_days = parse_uptime_string(output)

        health_status = "healthy"
        if uptime_days is not None:
            if uptime_days < 7:
                health_status = "recently_rebooted"
            elif uptime_days > 1825:  # ~5 years
                health_status = "long_uptime"

        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "platform": task.host.platform,
                "uptime_days": uptime_days,
                "status": "reachable",
                "health": health_status,
                "timestamp": datetime.now().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"Device {task.host.name} failed: {str(e)}")
        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "platform": task.host.platform,
                "uptime_days": None,
                "status": "unreachable",
                "health": "unknown",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            },
            failed=True
        )


def format_text_report(devices: List[Dict[str, Any]]) -> str:
    """Format results as human-readable text report."""
    lines = [
        "\n" + "=" * 80,
        f"{'Device':<20} {'Platform':<12} {'Uptime (Days)':<18} {'Health Status':<15}",
        "-" * 80
    ]

    for device in devices:
        uptime = device.get("uptime_days")
        uptime_str = f"{uptime:.1f}" if uptime is not None else "N/A"
        health = device.get("health", "unknown").replace("_", " ").title()

        lines.append(
            f"{device['device']:<20} {device.get('platform', 'N/A'):<12} "
            f"{uptime_str:<18} {health:<15}"
        )

    lines.append("=" * 80)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--group",
        help="Filter devices by inventory group"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=30,
        help="Alert if device uptime is below N days (default: 30)"
    )
    parser.add_argument(
        "--sort",
        choices=["name", "uptime", "health"],
        default="name",
        help="Sort output by field"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity"
    )

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        nr = InitNornir(config_file="config.yaml")
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} devices")

        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} devices in group '{args.group}'")

        results = nr.run(task=gather_device_uptime)
        devices = [r[0].result for r in results.values()]

        sort_key = {
            "name": lambda x: x["device"],
            "uptime": lambda x: x["uptime_days"] or 0,
            "health": lambda x: x["health"]
        }
        devices_sorted = sorted(devices, key=sort_key[args.sort])

        if args.format == "json":
            print(json.dumps(devices_sorted, indent=2))
        else:
            print(format_text_report(devices_sorted))
            recent = [d for d in devices_sorted if d.get("uptime_days", 999) < args.threshold]
            if recent:
                logger.warning(f"{len(recent)} device(s) rebooted in last {args.threshold} days")

    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```