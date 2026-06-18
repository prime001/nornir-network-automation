```python
"""
Device Health Monitor - Collects and reports device health metrics.

This script connects to network devices using Nornir and collects health
metrics such as uptime, CPU utilization, memory usage, and temperature
(when available via NAPALM get_environment()).

Usage:
    python device_health_monitor.py --devices core-routers
    python device_health_monitor.py --device r1.lab.local
    python device_health_monitor.py --devices all --format json

Prerequisites:
    - Nornir installation with NAPALM plugin
    - hosts.yml and defaults.yml configured
    - Device credentials in environment or nornir config
    - Devices must support NAPALM get_facts() and get_environment()

The script produces a summary report with key health indicators and flags
warnings/errors for unhealthy devices.
"""

import argparse
import json
import logging
import sys
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_device_health(task: Task) -> Result:
    """Collect device health metrics including facts and environment data."""
    try:
        facts_result = task.run(
            napalm_get,
            getters=["get_facts", "get_environment"],
        )
        
        facts_data = facts_result[0].result
        facts = facts_data.get("get_facts", {})
        environment = facts_data.get("get_environment", {})
        
        uptime_seconds = facts.get("uptime_seconds", 0)
        uptime_days = uptime_seconds // 86400
        uptime_hours = (uptime_seconds % 86400) // 3600
        
        cpu_percent = None
        if environment.get("cpu") and len(environment["cpu"]) > 0:
            cpu_percent = environment["cpu"][0].get("%usage")
        
        memory_percent = None
        if environment.get("memory"):
            mem = environment["memory"]
            available = mem.get("available_ram", 0)
            used = mem.get("used_ram", 0)
            total = available + used
            if total > 0:
                memory_percent = round((used / total) * 100, 1)
        
        max_temp = None
        temp_status = "normal"
        if environment.get("temperature"):
            temps = []
            for sensor, data in environment["temperature"].items():
                if isinstance(data, dict):
                    current = data.get("current_temperature")
                    if current:
                        temps.append(current)
                        critical = data.get("critical_threshold", 100)
                        if current > critical:
                            temp_status = "critical"
                        elif current > critical * 0.9:
                            temp_status = "warning"
            if temps:
                max_temp = max(temps)
        
        health_status = "healthy"
        warnings = []
        
        if uptime_days < 1:
            warnings.append(f"uptime_low ({uptime_hours}h)")
            health_status = "warning"
        
        if cpu_percent and cpu_percent > 80:
            warnings.append(f"cpu_high ({cpu_percent}%)")
            health_status = "warning"
        
        if memory_percent and memory_percent > 85:
            warnings.append(f"memory_high ({memory_percent}%)")
            health_status = "warning"
        
        if temp_status == "critical":
            warnings.append(f"temp_critical ({max_temp}C)")
            health_status = "critical"
        elif temp_status == "warning":
            warnings.append(f"temp_warning ({max_temp}C)")
            if health_status != "critical":
                health_status = "warning"
        
        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "status": health_status,
                "model": facts.get("model"),
                "version": facts.get("os_version"),
                "uptime_days": uptime_days,
                "uptime_hours": uptime_hours,
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "max_temperature": max_temp,
                "warnings": warnings,
            },
        )
    
    except Exception as e:
        logger.error(f"Error collecting health for {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "status": "error",
                "error": str(e),
            },
            failed=True,
        )


def print_table_report(health_data: list) -> None:
    """Print device health data in tabular format."""
    print("\n" + "=" * 120)
    print("DEVICE HEALTH MONITOR")
    print("=" * 120)
    print(f"{'Device':<20} {'Status':<12} {'Model':<20} {'Uptime':<12} {'CPU':<8} {'Memory':<10} {'Temp':<10}")
    print("-" * 120)
    
    for device in health_data:
        name = device.get("device", "unknown")[:19]
        status = device.get("status", "unknown").upper()[:11]
        model = (device.get("model") or "unknown")[:19]
        
        if device.get("error"):
            print(f"{name:<20} {status:<12} ERROR: {device['error']}")
        else:
            uptime = f"{device['uptime_days']}d {device['uptime_hours']}h"
            cpu = f"{device.get('cpu_percent', '-')}%"
            mem = f"{device.get('memory_percent', '-')}%"
            temp = f"{device.get('max_temperature', '-')}C"
            
            print(f"{name:<20} {status:<12} {model:<20} {uptime:<12} {cpu:<8} {mem:<10} {temp:<10}")
            
            if device.get("warnings"):
                print(f"  └─ Warnings: {', '.join(device['warnings'])}")
    
    print("=" * 120 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Monitor network device health metrics.")
    parser.add_argument("--devices", default="all", help="Device group or comma-separated list")
    parser.add_argument("--device", help="Single device hostname")
    parser.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    parser.add_argument("--config", default="~/.nornir/config.yaml", help="Nornir config file path")
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.config)
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        sys.exit(1)
    
    if args.device:
        nr = nr.filter(name=args.device)
    elif args.devices != "all":
        device_list = [d.strip() for d in args.devices.split(",")]
        nr = nr.filter(lambda h: h.name in device_list)
    
    if not nr.inventory.hosts:
        logger.error("No devices matched the filter criteria")
        sys.exit(1)
    
    logger.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")
    
    results = nr.run(task=get_device_health)
    
    health_data = [task_result[0].result for task_result in results.values()]
    
    if args.format == "json":
        print(json.dumps(health_data, indent=2))
    else:
        print_table_report(health_data)
    
    failed_count = sum(1 for d in health_data if d.get("status") in ("error", "critical"))
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
```