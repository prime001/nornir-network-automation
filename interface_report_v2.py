```python
#!/usr/bin/env python3
"""
Device Uptime Reporter - Collects and reports device uptime statistics.

Purpose:
    Gathers uptime information from network devices and generates reports
    showing device uptime, identifying devices with recent reboots or issues.

Usage:
    python device_uptime_report.py --warn-hours 168 --format text
    python device_uptime_report.py --group edge --sort-by uptime

Prerequisites:
    - nornir inventory configured with device connectivity parameters
    - SSH/Telnet access with appropriate credentials
    - Devices supporting 'show version' or 'show system uptime' commands
"""

import logging
import argparse
import json
import re
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_utils.plugins.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def parse_uptime_from_output(output: str) -> int:
    """Extract uptime in seconds from device output."""
    patterns = [
        r"uptime is (\d+)\s+days?,?\s+(\d+)\s+hours?,?\s+(\d+)\s+minutes?",
        r"(\d+)d(\d+)h(\d+)m",
        r"up\s+(\d+)\s+days?,?\s+(\d+):(\d+)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            days, hours, minutes = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return days * 86400 + hours * 3600 + minutes * 60
    
    return None


def format_uptime(seconds: int) -> str:
    """Format uptime seconds to human-readable format."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def collect_device_uptime(task: Task) -> Result:
    """Collect uptime information from a device."""
    uptime_info = {
        "hostname": task.host.name,
        "platform": task.host.platform,
        "uptime_seconds": None,
        "uptime_human": None,
        "status": "unknown",
        "error": None,
    }
    
    try:
        cmd = "show version" if "ios" in task.host.platform else "show system uptime"
        
        response = task.run(
            netmiko_send_command,
            command_string=cmd,
            use_textfsm=False
        )
        
        output = response[0].result
        uptime_seconds = parse_uptime_from_output(output)
        
        if uptime_seconds is not None:
            uptime_info["uptime_seconds"] = uptime_seconds
            uptime_info["uptime_human"] = format_uptime(uptime_seconds)
            uptime_info["status"] = "success"
        else:
            uptime_info["status"] = "parse_error"
        
        return Result(host=task.host, result=uptime_info)
        
    except Exception as e:
        uptime_info["status"] = "failed"
        uptime_info["error"] = str(e)
        logger.error(f"{task.host.name}: {e}")
        return Result(host=task.host, result=uptime_info, failed=True)


def main():
    parser = argparse.ArgumentParser(
        description="Collect and report device uptime across network inventory"
    )
    parser.add_argument(
        "--group",
        help="Filter devices by inventory group"
    )
    parser.add_argument(
        "--warn-hours",
        type=int,
        default=168,
        help="Alert threshold if uptime less than N hours"
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format"
    )
    parser.add_argument(
        "--sort-by",
        choices=["uptime", "hostname"],
        default="uptime",
        help="Sort results by field"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.group:
            nr = nr.filter(group=args.group)
        
        device_count = len(nr.inventory.hosts)
        if device_count == 0:
            logger.error(f"No devices found matching filter: {args.group}")
            return 1
        
        logger.info(f"Collecting uptime from {device_count} device(s)")
        results = nr.run(task=collect_device_uptime)
        
        devices = []
        for hostname, task_results in results.items():
            for result in task_results:
                devices.append(result.result)
        
        if args.sort_by == "uptime":
            devices.sort(key=lambda x: x.get("uptime_seconds") or 0)
        else:
            devices.sort(key=lambda x: x["hostname"])
        
        if args.format == "json":
            print(json.dumps(devices, indent=2))
        else:
            print("\n" + "=" * 85)
            print("Device Uptime Report")
            print("=" * 85)
            print(f"{'Hostname':<25} {'Uptime':<20} {'Status':<15} {'Alert':<10}")
            print("-" * 85)
            
            warn_seconds = args.warn_hours * 3600
            alert_count = 0
            success_count = 0
            
            for device in devices:
                status = device["status"]
                uptime = device["uptime_human"] or "N/A"
                alert = ""
                
                if status == "success":
                    success_count += 1
                    if device.get("uptime_seconds", 0) < warn_seconds:
                        alert = "⚠ LOW"
                        alert_count += 1
                
                print(f"{device['hostname']:<25} {uptime:<20} {status:<15} {alert:<10}")
            
            print("=" * 85)
            print(f"Summary: {success_count} successful, {alert_count} alerts, "
                  f"{len(devices) - success_count} failed")
        
        return 0 if alert_count == 0 else 1
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```