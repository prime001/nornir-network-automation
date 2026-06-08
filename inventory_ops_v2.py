```python
"""
Device Uptime and Restart Tracker

Purpose: Monitor device uptime and detect recent unexpected restarts.

Usage:
  python uptime_tracker.py --config config.yaml --output json
  python uptime_tracker.py --filter site:dc1 --threshold 24
  python uptime_tracker.py --devices router1,router2 --verbose

Prerequisites:
- Nornir installed and configured with netmiko
- Credentials in environment (NORNIR_USERNAME, NORNIR_PASSWORD)
- Device inventory with hostnames reachable via SSH

Output formats: text (default), json, csv
Threshold: Alert if uptime less than N hours (default 168 = 7 days)
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from nornir import InitNornir
from nornir.core.task import Task
from nornir.plugins.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def parse_uptime(output: str) -> int:
    """Parse uptime in hours from show version output."""
    try:
        # Look for uptime patterns like "10 days, 3 hours" or "3 hours, 45 minutes"
        lines = output.lower().split('\n')
        for line in lines:
            if 'uptime' in line:
                hours = 0
                if 'day' in line:
                    days = int(line.split()[0])
                    hours += days * 24
                if 'hour' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part.startswith('hour'):
                            hours += int(parts[i-1])
                            break
                return hours
        return -1
    except Exception as e:
        logger.error(f"Failed to parse uptime: {e}")
        return -1


def collect_uptime(task: Task) -> Dict:
    """Collect device uptime information."""
    device_data = {
        "name": task.host.name,
        "hostname": task.host.hostname,
        "platform": task.host.platform or "unknown",
        "status": "unreachable",
        "uptime_hours": None,
        "error": None
    }
    
    try:
        result = task.run(netmiko_send_command, command_string="show version")
        output = result[0].result
        
        uptime_hours = parse_uptime(output)
        if uptime_hours >= 0:
            device_data["status"] = "reachable"
            device_data["uptime_hours"] = uptime_hours
        else:
            device_data["status"] = "parse_failed"
            device_data["error"] = "Could not parse uptime from output"
            
    except Exception as e:
        device_data["status"] = "unreachable"
        device_data["error"] = str(e)
        logger.warning(f"Connection failed for {task.host.name}: {e}")
    
    return device_data


def format_text_output(devices: List[Dict], threshold_hours: int) -> str:
    """Format output as human-readable text."""
    lines = [
        "=" * 80,
        "Device Uptime and Restart Tracker",
        f"Threshold: {threshold_hours} hours ({threshold_hours / 24:.1f} days)",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 80,
        ""
    ]
    
    restarted = []
    for dev in devices:
        status_symbol = "✓" if dev["status"] == "reachable" else "✗"
        lines.append(f"{status_symbol} {dev['name']:<20} ({dev['hostname']})")
        
        if dev["status"] == "reachable":
            hours = dev["uptime_hours"]
            days = hours // 24
            remaining_hours = hours % 24
            lines.append(f"  Uptime: {days}d {remaining_hours}h ({hours}h total)")
            
            if hours < threshold_hours:
                lines.append(f"  ⚠️  ALERT: Uptime below threshold!")
                restarted.append(dev["name"])
        else:
            lines.append(f"  Status: {dev['status']}")
            if dev["error"]:
                lines.append(f"  Error: {dev['error'][:60]}")
        lines.append("")
    
    if restarted:
        lines.append(f"\nDevices with recent restarts: {', '.join(restarted)}")
    
    return "\n".join(lines)


def format_json_output(devices: List[Dict]) -> str:
    """Format output as JSON."""
    return json.dumps(devices, indent=2, default=str)


def format_csv_output(devices: List[Dict]) -> str:
    """Format output as CSV."""
    lines = ["Name,Hostname,Platform,Status,Uptime_Hours,Error"]
    for dev in devices:
        error = dev.get("error", "").replace(",", ";") if dev.get("error") else ""
        lines.append(
            f"{dev['name']},{dev['hostname']},{dev['platform']},"
            f"{dev['status']},{dev['uptime_hours']},{error}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Track device uptime and detect unexpected restarts"
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Nornir config file path (default: config.yaml)"
    )
    parser.add_argument(
        "-f", "--filter",
        help="Filter devices by attribute (e.g., 'site:dc1', 'role:router')"
    )
    parser.add_argument(
        "-d", "--devices",
        help="Comma-separated device names (overrides filter)"
    )
    parser.add_argument(
        "-t", "--threshold",
        type=int,
        default=168,
        help="Alert threshold in hours (default: 168 = 7 days)"
    )
    parser.add_argument(
        "-o", "--output",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    try:
        nr = InitNornir(config_file=args.config)
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name=lambda x: x in device_list)
        elif args.filter:
            key, value = args.filter.split(":", 1)
            nr = nr.filter(**{key: value})
        
        if not nr.inventory.hosts:
            logger.error("No devices found matching filter")
            sys.exit(1)
        
        logger.info(f"Collecting uptime from {len(nr.inventory.hosts)} devices")
        
        device_data = []
        for device_name in sorted(nr.inventory.hosts.keys()):
            host = nr.inventory.hosts[device_name]
            data = collect_uptime(host)
            device_data.append(data)
        
        if args.output == "json":
            output = format_json_output(device_data)
        elif args.output == "csv":
            output = format_csv_output(device_data)
        else:
            output = format_text_output(device_data, args.threshold)
        
        print(output)
        logger.info("Collection complete")
        
    except FileNotFoundError:
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
```