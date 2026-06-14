```python
#!/usr/bin/env python
"""
Device health check and status monitoring using Nornir.

Purpose:
    Performs comprehensive health checks on network devices including reachability,
    operational metrics, and system uptime. Generates structured reports with
    pass/fail status based on configurable thresholds.

Usage:
    python device_health_check.py --hosts router1,router2 --min-uptime 3600
    python device_health_check.py --group core --output health_report.json
    python device_health_check.py --tags critical --workers 8

Prerequisites:
    - Nornir with netmiko plugin
    - Device inventory in config.yaml (hosts.yaml, groups.yaml)
    - SSH credentials configured (inventory or environment)
    - Network devices reachable and responsive to 'show version'
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def parse_uptime(uptime_string: str) -> int:
    """Parse device uptime string and return total seconds."""
    try:
        total_seconds = 0
        parts = uptime_string.split(',')

        for part in parts:
            part = part.strip()
            tokens = part.split()

            if len(tokens) >= 2:
                value = int(tokens[0])
                unit = tokens[1].lower()

                if unit.startswith('week'):
                    total_seconds += value * 604800
                elif unit.startswith('day'):
                    total_seconds += value * 86400
                elif unit.startswith('hour'):
                    total_seconds += value * 3600
                elif unit.startswith('minute'):
                    total_seconds += value * 60

        return total_seconds
    except (ValueError, IndexError, AttributeError):
        return 0


def check_device_health(task: Task, min_uptime_seconds: int) -> Result:
    """
    Execute health check on device.

    Collects uptime, model, OS version, and reachability.
    Returns health metrics with compliance status.
    """
    health_data: Dict[str, Any] = {
        "device": task.host.name,
        "timestamp": datetime.utcnow().isoformat(),
        "reachable": False,
        "uptime_seconds": 0,
        "uptime_string": "Unknown",
        "model": "Unknown",
        "os_version": "Unknown",
        "status": "UNKNOWN",
        "errors": []
    }

    try:
        command_result = task.run(
            netmiko_send_command,
            command_string="show version",
            use_textfsm=False
        )

        health_data["reachable"] = True
        version_output = command_result[0].result

        for line in version_output.split('\n'):
            line_lower = line.lower()

            if 'uptime' in line_lower:
                uptime_part = line.split('is')[-1].strip() if 'is' in line else line
                health_data["uptime_string"] = uptime_part
                health_data["uptime_seconds"] = parse_uptime(uptime_part)

            elif 'model' in line_lower or 'device id' in line_lower:
                health_data["model"] = line.split(':')[-1].strip()

            elif 'version' in line_lower or 'ios' in line_lower:
                health_data["os_version"] = line.split(':')[-1].strip()

        if health_data["uptime_seconds"] >= min_uptime_seconds:
            health_data["status"] = "PASS"
        else:
            health_data["status"] = "WARN"
            health_data["errors"].append(
                f"Low uptime: {health_data['uptime_seconds']}s < {min_uptime_seconds}s"
            )

    except Exception as error:
        health_data["status"] = "FAIL"
        health_data["errors"].append(str(error))
        logger.error(f"{task.host.name}: {error}")

    return Result(host=task.host, result=health_data)


def main():
    parser = argparse.ArgumentParser(
        description="Device health check and monitoring tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--hosts',
        type=str,
        help='Comma-separated device names'
    )
    parser.add_argument(
        '--group',
        type=str,
        help='Filter by host group'
    )
    parser.add_argument(
        '--tags',
        type=str,
        help='Comma-separated tags to filter'
    )
    parser.add_argument(
        '--min-uptime',
        type=int,
        default=3600,
        help='Minimum uptime seconds (default: 3600)'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Save JSON report to file'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=4,
        help='Parallel workers (default: 4)'
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file="config.yaml")
    except Exception as error:
        logger.error(f"Failed to initialize Nornir: {error}")
        sys.exit(1)

    if args.hosts:
        host_list = [h.strip() for h in args.hosts.split(',')]
        nr = nr.filter(F(name__in=host_list))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))
    elif args.tags:
        tag_set = set(t.strip() for t in args.tags.split(','))
        nr = nr.filter(F(tags__contains=tag_set))

    if not nr.inventory.hosts:
        logger.error("No hosts matched criteria")
        sys.exit(1)

    logger.info(f"Checking {len(nr.inventory.hosts)} device(s)")

    results = nr.run(
        task=check_device_health,
        min_uptime_seconds=args.min_uptime,
        num_workers=args.workers
    )

    health_report = []
    failed_count = 0

    for hostname, multi_result in results.items():
        for result in multi_result:
            if isinstance(result.result, dict):
                health_report.append(result.result)
                status = result.result["status"]
                uptime = result.result["uptime_string"]

                if status == "PASS":
                    logger.info(f"✓ {hostname}: {status} (uptime: {uptime})")
                else:
                    logger.warning(f"✗ {hostname}: {status} (uptime: {uptime})")
                    if status == "FAIL":
                        failed_count += 1

    if args.output:
        try:
            with open(args.output, 'w') as f:
                json.dump(health_report, f, indent=2)
            logger.info(f"Report saved: {args.output}")
        except IOError as error:
            logger.error(f"Failed to write report: {error}")

    logger.info(f"{len(health_report)} checked, {failed_count} failed")
    sys.exit(1 if failed_count > 0 else 0)


if __name__ == "__main__":
    main()
```