```python
#!/usr/bin/env python3
"""
Device Health Check and Monitoring Script

Gathers and reports device health metrics including uptime, CPU utilization,
memory usage, and system temperature across network devices. Identifies devices
requiring attention and provides exportable health reports.

Usage:
    python device_health_check.py --group core
    python device_health_check.py --device router1 --format json
    python device_health_check.py --all --format csv

Prerequisites:
    - Nornir and NAPALM installed
    - Inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
    - Device credentials configured
    - Network device connectivity

Output:
    - Formatted console report (text/JSON/CSV)
    - Optional file export for further analysis
"""

import argparse
import json
import logging
from pathlib import Path
from datetime import datetime
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the script."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def get_device_health(task: Task) -> Result:
    """Retrieve device health facts using NAPALM."""
    result = task.run(
        napalm_get,
        getters=["facts", "environment"]
    )
    return result


def extract_health_metrics(facts: dict, environment: dict) -> dict:
    """Extract and normalize health metrics from device data."""
    uptime_sec = facts.get("uptime", 0)
    uptime_days = round(uptime_sec / 86400, 2)
    
    memory = environment.get("memory", {})
    cpu = environment.get("cpu", {})
    
    metrics = {
        "vendor": facts.get("vendor", "Unknown"),
        "model": facts.get("model", "Unknown"),
        "os_version": facts.get("os_version", "Unknown"),
        "uptime_days": uptime_days,
        "uptime_seconds": uptime_sec,
    }
    
    if memory:
        metrics["memory_used_mb"] = memory.get("used_ram", "N/A")
        metrics["memory_free_mb"] = memory.get("available_ram", "N/A")
    
    if cpu:
        cpu_entry = list(cpu.values())[0] if cpu else {}
        metrics["cpu_usage_percent"] = cpu_entry.get("%usage", "N/A")
    
    temps = environment.get("temperature", {})
    if temps:
        temp_entry = list(temps.values())[0]
        metrics["temperature_c"] = temp_entry.get("current_temperature", "N/A")
        metrics["temp_threshold_c"] = temp_entry.get("critical_threshold", "N/A")
    
    return metrics


def format_text_report(results: dict) -> str:
    """Format results as human-readable text."""
    lines = [
        "\n" + "="*90,
        "NETWORK DEVICE HEALTH CHECK REPORT",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "="*90 + "\n"
    ]
    
    for hostname in sorted(results.keys()):
        data = results[hostname]
        if "error" in data:
            lines.append(f"{hostname}: ERROR - {data['error']}")
            continue
        
        lines.append(f"Device: {hostname}")
        lines.append(f"  Vendor/Model: {data['vendor']} {data['model']}")
        lines.append(f"  OS Version: {data['os_version']}")
        lines.append(f"  Uptime: {data['uptime_days']} days")
        lines.append(f"  CPU Usage: {data['cpu_usage_percent']}%")
        lines.append(f"  Memory: {data['memory_used_mb']}MB used / {data['memory_free_mb']}MB free")
        
        if data.get("temperature_c") != "N/A":
            lines.append(f"  Temperature: {data['temperature_c']}°C (Threshold: {data['temp_threshold_c']}°C)")
        lines.append("")
    
    lines.append("="*90)
    return "\n".join(lines)


def main() -> None:
    """Execute health check against target devices."""
    parser = argparse.ArgumentParser(
        description="Check device health and system metrics across network devices"
    )
    parser.add_argument("--device", type=str, help="Target specific device by name")
    parser.add_argument("--group", type=str, help="Target specific group")
    parser.add_argument("--all", action="store_true", help="Target all devices")
    parser.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--output",
        type=str,
        help="Save report to file"
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    try:
        nr = InitNornir()
        logger.info("Nornir initialized")
        
        if args.device:
            nr = nr.filter(name=args.device)
        elif args.group:
            nr = nr.filter(group_name=args.group)
        elif not args.all:
            logger.error("Specify --device, --group, or --all")
            return
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter criteria")
            return
        
        logger.info(f"Executing health check on {len(nr.inventory.hosts)} device(s)")
        
        results_obj = nr.run(task=get_device_health)
        
        health_data = {}
        for hostname, task_result in results_obj.items():
            if task_result.failed:
                logger.warning(f"Failed to retrieve data from {hostname}")
                health_data[hostname] = {"error": "Connection failed"}
                continue
            
            facts = task_result[0].result.get("facts", {})
            environment = task_result[0].result.get("environment", {})
            health_data[hostname] = extract_health_metrics(facts, environment)
        
        if args.format == "json":
            output = json.dumps(health_data, indent=2, default=str)
        elif args.format == "csv":
            if not health_data or "error" in list(health_data.values())[0]:
                output = "Failed to retrieve device metrics"
            else:
                headers = list(health_data[list(health_data.keys())[0]].keys())
                lines = [",".join(headers)]
                for hostname, metrics in sorted(health_data.items()):
                    values = [str(metrics.get(h, "N/A")) for h in headers]
                    lines.insert(0, hostname)
                    lines.append(",".join([hostname] + values))
                output = "\n".join(lines[1:])
        else:
            output = format_text_report(health_data)
        
        print(output)
        
        if args.output:
            Path(args.output).write_text(output)
            logger.info(f"Report saved to {args.output}")
    
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
```