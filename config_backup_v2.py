```python
"""
Device Uptime and Boot History Analyzer

Purpose: Collect device uptime data, detect unexpected reboots, and 
         generate uptime/reliability reports.

Usage:
    python device_uptime_analyzer.py --devices spine1,spine2 \
                                     --username admin \
                                     --password secret123 \
                                     --baseline baseline.json

Prerequisites:
    - Nornir installed
    - NAPALM or device drivers configured
    - Devices must support "facts" getter
"""

import logging
import argparse
import json
from datetime import datetime
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.napalm import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_uptime(task: Task) -> Result:
    """Collect device uptime and boot information."""
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        
        if facts_result.failed:
            return Result(
                host=task.host,
                failed=True,
                result="Failed to retrieve device facts"
            )
        
        facts = facts_result.result.get("facts", {})
        
        uptime_data = {
            "device": task.host.name,
            "os_version": facts.get("os_version", "unknown"),
            "uptime_seconds": facts.get("uptime", 0),
            "uptime_days": facts.get("uptime", 0) // 86400,
            "hostname": facts.get("hostname", "unknown"),
            "timestamp": datetime.now().isoformat(),
        }
        
        logger.info(
            f"{task.host.name}: uptime {uptime_data['uptime_days']} days"
        )
        
        return Result(host=task.host, result=uptime_data)
    
    except Exception as e:
        logger.error(f"Error collecting uptime from {task.host.name}: {e}")
        return Result(host=task.host, failed=True, result=str(e))


def analyze_uptime(nr, baseline_file=None):
    """Collect and analyze device uptime data."""
    logger.info("Collecting device uptime data...")
    results = nr.run(task=collect_uptime)
    
    current_data = {}
    for host, task_result in results.items():
        if task_result[0].result:
            current_data[host] = task_result[0].result
    
    baseline_data = {}
    if baseline_file:
        try:
            with open(baseline_file, 'r') as f:
                baseline_data = json.load(f)
        except FileNotFoundError:
            logger.warning(f"Baseline file {baseline_file} not found")
    
    output = "Device Uptime Report\n" + "=" * 60 + "\n"
    reboot_alerts = []
    
    for device, uptime_info in current_data.items():
        output += f"\n{device}:\n"
        output += f"  OS: {uptime_info.get('os_version', 'N/A')}\n"
        output += f"  Uptime: {uptime_info.get('uptime_days', 0)} days "
        output += f"({uptime_info.get('uptime_seconds', 0)} seconds)\n"
        
        if device in baseline_data:
            baseline_uptime = baseline_data[device].get("uptime_seconds", 0)
            current_uptime = uptime_info.get("uptime_seconds", 0)
            
            if current_uptime < baseline_uptime:
                reboot_alerts.append(
                    f"{device}: Possible reboot detected "
                    f"(was {baseline_uptime}s, now {current_uptime}s)"
                )
                output += f"  ⚠ REBOOT DETECTED\n"
    
    if reboot_alerts:
        output += "\n" + "=" * 60 + "\n"
        output += "REBOOT ALERTS:\n"
        for alert in reboot_alerts:
            output += f"  • {alert}\n"
    
    return output, current_data


def save_baseline(data, output_file):
    """Save current uptime data as baseline."""
    try:
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Baseline saved to {output_file}")
    except Exception as e:
        logger.error(f"Failed to save baseline: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze device uptime and detect reboots"
    )
    parser.add_argument(
        "--devices",
        type=str,
        default="",
        help="Comma-separated device names"
    )
    parser.add_argument(
        "--username",
        type=str,
        help="Username for device access"
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Password for device access"
    )
    parser.add_argument(
        "--baseline",
        type=str,
        help="Baseline uptime file for comparison"
    )
    parser.add_argument(
        "--save-baseline",
        type=str,
        help="Save current uptime as baseline to file"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name__in=device_list)
        
        if args.username:
            for host in nr.inventory.hosts.values():
                host.username = args.username
        if args.password:
            for host in nr.inventory.hosts.values():
                host.password = args.password
        
        report, current_data = analyze_uptime(nr, baseline_file=args.baseline)
        print(report)
        
        if args.save_baseline:
            save_baseline(current_data, args.save_baseline)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
```