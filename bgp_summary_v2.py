```python
"""
Device Uptime and Reboot Monitor

Tracks device uptime across the network and alerts when devices have been
recently rebooted unexpectedly. Useful for identifying hardware issues,
scheduled maintenance completion, or unexpected restarts.

Purpose:
  - Monitor device uptime trends
  - Alert on devices with low uptime (recent reboot)
  - Track reboots over time
  - Generate device health status reports

Prerequisites:
  - Nornir with netmiko installed
  - Network inventory configured with SSH credentials
  - Devices supporting "show version" command output

Usage:
  python 060_uptime_monitor.py --hosts all
  python 060_uptime_monitor.py --hosts core_routers --threshold 30
  python 060_uptime_monitor.py --hosts branch --threshold 7 --verbose

Author: Network Automation
License: MIT
"""

import argparse
import logging
import sys
import re
from datetime import timedelta
from typing import Dict, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def extract_uptime(output: str) -> Optional[timedelta]:
    """Extract uptime from show version command output."""
    patterns = [
        r"uptime is (\d+)\s*year[s]?\s*,\s*(\d+)\s*week[s]?\s*,\s*(\d+)\s*day[s]?\s*,\s*(\d+)\s*hour[s]?\s*,\s*(\d+)\s*minute[s]?",
        r"uptime is (\d+)\s*day[s]?\s*,\s*(\d+)\s*hour[s]?\s*,\s*(\d+)\s*minute[s]?",
        r"System uptime:\s*(\d+)\s*day[s]?\s*,\s*(\d+)\s*hour[s]?\s*,\s*(\d+)\s*minute[s]?",
    ]

    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            groups = [int(g) for g in match.groups()]

            if len(groups) == 5:
                years, weeks, days, hours, minutes = groups
                return timedelta(
                    days=years * 365 + weeks * 7 + days,
                    hours=hours,
                    minutes=minutes,
                )
            elif len(groups) == 3:
                days, hours, minutes = groups
                return timedelta(days=days, hours=hours, minutes=minutes)

    return None


def check_device_uptime(task: Task, threshold_days: int = 7) -> Result:
    """Check and report device uptime."""
    try:
        logger.debug(f"Fetching uptime from {task.host.name}")

        result = task.run(
            netmiko_send_command,
            command_string="show version",
        )

        uptime = extract_uptime(result.result)

        if not uptime:
            logger.warning(f"Could not parse uptime on {task.host.name}")
            return Result(
                host=task.host,
                result={"error": "Unable to parse uptime"},
                failed=True,
            )

        uptime_days = uptime.days
        needs_alert = uptime_days < threshold_days
        status = "ALERT" if needs_alert else "OK"

        logger.info(
            f"{task.host.name}: {status} - {uptime} ({uptime_days} days)"
        )

        return Result(
            host=task.host,
            result={
                "uptime_days": uptime_days,
                "uptime_formatted": str(uptime),
                "needs_alert": needs_alert,
                "status": status,
            },
        )

    except Exception as e:
        logger.error(f"Error checking {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={"error": str(e)},
            failed=True,
        )


def generate_report(results: Dict, threshold_days: int) -> str:
    """Generate formatted uptime report."""
    lines = ["=" * 60]
    lines.append("DEVICE UPTIME REPORT")
    lines.append(f"Alert Threshold: {threshold_days} days")
    lines.append("=" * 60)

    alert_devices = []
    ok_devices = []

    for host_name, task_results in sorted(results.items()):
        task_result = task_results[0].result

        if task_result.get("error"):
            lines.append(f"{host_name:30} ERROR: {task_result['error']}")
            continue

        uptime_str = task_result["uptime_formatted"]
        status = task_result["status"]
        uptime_days = task_result["uptime_days"]

        if status == "ALERT":
            alert_devices.append(f"{host_name:30} {uptime_str:20} ({uptime_days} days)")
        else:
            ok_devices.append(f"{host_name:30} {uptime_str:20} ({uptime_days} days)")

    if alert_devices:
        lines.append("\n[ALERT] Devices with uptime below threshold:")
        lines.append("-" * 60)
        lines.extend(alert_devices)

    lines.append("\n[OK] Devices with acceptable uptime:")
    lines.append("-" * 60)
    lines.extend(ok_devices if ok_devices else ["  None or all devices require attention"])

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor network device uptime and detect recent reboots",
        epilog="Use --hosts to filter devices by name or group.",
    )
    parser.add_argument(
        "--hosts",
        default="all",
        help="Host name or group to check (default: all)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=7,
        help="Alert if uptime below N days (default: 7)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        logger.info("Initializing Nornir inventory")
        nr = InitNornir(config_file="config.yaml")

        if args.hosts.lower() != "all":
            nr = nr.filter(F(groups__contains=args.hosts) | F(name=args.hosts))

        if not nr.inventory.hosts:
            logger.error(f"No hosts matched filter: {args.hosts}")
            sys.exit(1)

        logger.info(f"Checking uptime on {len(nr.inventory.hosts)} devices")

        results = nr.run(
            task=check_device_uptime,
            threshold_days=args.threshold,
        )

        report = generate_report(dict(results), args.threshold)
        print(report)

        failed_hosts = [h for h, r in results.items() if r[0].failed]
        if failed_hosts:
            logger.warning(f"Failed to check {len(failed_hosts)} host(s)")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```