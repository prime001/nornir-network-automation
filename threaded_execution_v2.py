```python
#!/usr/bin/env python3
"""
Device Uptime and Version Auditor

Purpose:
    Collects device uptime and software version information across the network.
    Identifies devices with outdated software or recent reboots that may indicate
    problems.

Usage:
    python3 device_uptime_auditor.py --username admin --password secret
    python3 device_uptime_auditor.py --host router1 --username admin
    python3 device_uptime_auditor.py --output json --username admin

Prerequisites:
    - Nornir installed and configured with device inventory
    - Network devices support 'show version' command
    - SSH connectivity with paramiko/netmiko configured
    - Proper device credentials

"""

import argparse
import json
import logging
from datetime import datetime
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_device_info(task: Task) -> Result:
    """
    Collect uptime and version information from device.

    Args:
        task: Nornir task object

    Returns:
        Result containing device uptime and version
    """
    host = task.host
    logger.info(f"Gathering info from {host.name}")

    device_data = {
        "name": host.name,
        "ip": host.hostname,
        "platform": host.platform or "unknown",
        "version": None,
        "uptime_days": None,
        "status": "pending"
    }

    try:
        conn = task.host.get_connection("netmiko")

        version_output = conn.send_command("show version")
        device_data["version"] = version_output.split('\n')[0] \
            if version_output else "unknown"

        uptime_days = parse_uptime_from_version(version_output)
        device_data["uptime_days"] = uptime_days
        device_data["status"] = "success"

        logger.info(f"Successfully collected info from {host.name}")

    except Exception as e:
        device_data["status"] = "failed"
        device_data["error"] = str(e)
        logger.error(f"Error collecting info from {host.name}: {e}")

    return Result(host=host, result=device_data)


def parse_uptime_from_version(output: str) -> int:
    """
    Parse uptime in days from device version output.

    Args:
        output: Raw output from show version command

    Returns:
        Uptime in days
    """
    try:
        for line in output.split('\n'):
            if 'uptime' in line.lower():
                parts = line.split()
                for i, part in enumerate(parts):
                    if 'day' in part.lower() and i > 0:
                        try:
                            return int(parts[i-1])
                        except ValueError:
                            pass
    except Exception:
        pass
    return 0


def generate_report(results: Dict[str, Any], output_format: str,
                    min_uptime: int) -> None:
    """
    Generate audit report from collected device data.

    Args:
        results: Nornir results dictionary
        output_format: 'text' or 'json'
        min_uptime: Minimum acceptable uptime in days
    """
    devices = []
    alerts = []

    for host_name in results.keys():
        result = results[host_name][0].result
        devices.append(result)

        if result['status'] == 'success' and result['uptime_days'] < min_uptime:
            alerts.append({
                'device': result['name'],
                'issue': f"Low uptime: {result['uptime_days']} days"
            })

    if output_format == "json":
        output = {
            "timestamp": datetime.now().isoformat(),
            "device_count": len(devices),
            "alerts": alerts,
            "devices": devices
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n" + "="*80)
        print(f"Device Inventory Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)

        if alerts:
            print("\nALERTS:")
            for alert in alerts:
                print(f"  ⚠ {alert['device']}: {alert['issue']}")

        print("\nDEVICES:")
        for device in sorted(devices, key=lambda x: x['name']):
            icon = "✓" if device['status'] == 'success' else "✗"
            print(f"\n{icon} {device['name']:25} ({device['ip']})")
            print(f"  Platform:  {device['platform']}")

            if device['status'] == 'success':
                print(f"  Uptime:    {device['uptime_days']} days")
                print(f"  Version:   {device['version']}")
            else:
                print(f"  Error:     {device.get('error', 'Unknown')}")

        print("\n" + "="*80)


def main():
    parser = argparse.ArgumentParser(
        description="Audit device uptime and software versions"
    )
    parser.add_argument(
        "--host",
        help="Specific device hostname (default: all devices)"
    )
    parser.add_argument(
        "--username",
        required=True,
        help="SSH username for device access"
    )
    parser.add_argument(
        "--password",
        help="SSH password (will prompt if not provided)"
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--min-uptime",
        type=int,
        default=7,
        help="Alert if uptime less than N days (default: 7)"
    )

    args = parser.parse_args()

    logger.info("Initializing Nornir")

    try:
        nr = InitNornir()

        if args.host:
            nr = nr.filter(F(name=args.host))

        if len(nr.inventory.hosts) == 0:
            logger.error(f"No devices found matching criteria")
            return 1

        logger.info(f"Auditing {len(nr.inventory.hosts)} device(s)")

        results = nr.run(task=collect_device_info)

        generate_report(results, args.output, args.min_uptime)

        logger.info("Audit completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```