```python
#!/usr/bin/env python
"""
Device Health Report Generator

Collects system metrics and health status from network devices using Nornir and NAPALM.
Generates a comprehensive health report showing CPU, memory, uptime, and interface statistics.

Prerequisites:
    - Nornir with NAPALM plugin installed
    - Network devices accessible via SSH/Paramiko
    - NAPALM drivers for target device OS types
    
Usage:
    python device_health_report.py --file inventory.yaml --group routers
    python device_health_report.py --file inventory.yaml --device router1
    python device_health_report.py --file inventory.yaml --output health_report.json

"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.napalm_utils import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_device_health(task: Task) -> Result:
    """Collect system health metrics from device using NAPALM."""
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        interfaces_result = task.run(napalm_get, getters=["interfaces"])
        
        facts = facts_result[0].result.get("facts", {})
        interfaces = interfaces_result[0].result.get("interfaces", {})
        
        # Calculate uptime string
        uptime_seconds = facts.get("uptime", 0)
        uptime_days = uptime_seconds // 86400
        uptime_hours = (uptime_seconds % 86400) // 3600
        uptime_str = f"{uptime_days}d {uptime_hours}h"
        
        # Count interface status
        total_ifaces = len(interfaces)
        up_ifaces = sum(1 for iface in interfaces.values() if iface.get("is_up"))
        
        health_data = {
            "device": task.host.name,
            "timestamp": datetime.now().isoformat(),
            "system_info": {
                "vendor": facts.get("vendor", "Unknown"),
                "model": facts.get("model", "Unknown"),
                "os_version": facts.get("os_version", "Unknown"),
                "serial_number": facts.get("serial_number", "N/A"),
                "hostname": facts.get("hostname", "Unknown"),
            },
            "health_metrics": {
                "uptime": uptime_str,
                "uptime_seconds": uptime_seconds,
                "cpu_used_percent": facts.get("cpu_used", -1),
                "memory_used_percent": facts.get("memory_used_percent", -1),
            },
            "interface_stats": {
                "total_count": total_ifaces,
                "up_count": up_ifaces,
                "down_count": total_ifaces - up_ifaces,
                "up_percentage": round(
                    (up_ifaces / total_ifaces * 100) if total_ifaces > 0 else 0, 2
                ),
            },
        }
        
        return Result(host=task.host, result=health_data)
        
    except Exception as e:
        logger.error(f"Error collecting health data from {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={"error": str(e)},
            failed=True,
        )


def print_report(results: dict) -> None:
    """Print formatted health report."""
    print("\n" + "=" * 90)
    print("DEVICE HEALTH REPORT".center(90))
    print("=" * 90 + "\n")
    
    for device_name, data in results.items():
        if "error" in data:
            print(f"❌ {device_name}: {data['error']}\n")
            continue
        
        info = data["system_info"]
        metrics = data["health_metrics"]
        ifaces = data["interface_stats"]
        
        print(f"Device: {device_name}")
        print(f"  {info['vendor']} {info['model']} | OS: {info['os_version']}")
        print(f"  Uptime: {metrics['uptime']} | CPU: {metrics['cpu_used_percent']}% | "
              f"Memory: {metrics['memory_used_percent']}%")
        print(f"  Interfaces: {ifaces['up_count']}/{ifaces['total_count']} up "
              f"({ifaces['up_percentage']}%)")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Collect and report device health metrics from network inventory"
    )
    parser.add_argument(
        "--file", "-f",
        default="inventory.yaml",
        help="Path to Nornir inventory file"
    )
    parser.add_argument(
        "--device", "-d",
        help="Target specific device by name"
    )
    parser.add_argument(
        "--group", "-g",
        help="Target devices in specific group"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output results to JSON file"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nornir = InitNornir(config_file=args.file)
        logger.info(f"Loaded inventory from {args.file}")
        
        if args.device:
            nornir = nornir.filter(name=args.device)
        elif args.group:
            nornir = nornir.filter(group=args.group)
        
        if not nornir.inventory.hosts:
            logger.error("No devices matched criteria")
            sys.exit(1)
        
        logger.info(f"Targeting {len(nornir.inventory.hosts)} device(s)")
        
        results = nornir.run(task=get_device_health)
        
        health_results = {}
        for device_name, task_result in results.items():
            if task_result[0].result:
                health_results[device_name] = task_result[0].result
        
        print_report(health_results)
        
        if args.output:
            with open(args.output, "w") as f:
                json.dump(health_results, f, indent=2)
            logger.info(f"Report saved to {args.output}")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```