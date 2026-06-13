```python
"""
Device health and system status collector using Nornir.

Purpose:
  Collects and reports health metrics from network devices including
  system uptime, environmental sensors, and device reachability status.

Usage:
  python 011_device_health_check.py --devices all
  python 011_device_health_check.py --devices site1 --output json

Prerequisites:
  - Nornir with NAPALM plugin configured
  - SSH access to all devices
  - Inventory properly configured with hosts.yaml and groups.yaml
"""

import argparse
import json
import logging
from datetime import datetime

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import napalm_get
from nornir.core.filter import F


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_health(task: Task) -> Result:
    """Gather device health metrics using NAPALM."""
    try:
        logger.info(f"Collecting health from {task.host.name}...")
        
        facts_result = task.run(napalm_get, getters=["facts"])
        env_result = task.run(napalm_get, getters=["environment"])
        
        facts = facts_result[0].result.get("facts", {})
        environment = env_result[0].result.get("environment", {})
        
        status = "HEALTHY"
        uptime = facts.get("uptime_seconds", 0)
        
        if uptime < 3600:
            status = "CRITICAL"
        
        for psu_name, psu_data in environment.get("power", {}).items():
            if isinstance(psu_data, dict) and not psu_data.get("status", True):
                status = "CRITICAL"
        
        for fan_name, fan_data in environment.get("fans", {}).items():
            if isinstance(fan_data, dict) and not fan_data.get("status", True):
                status = "WARNING"
        
        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "reachable": True,
                "status": status,
                "uptime_seconds": uptime,
                "model": facts.get("model", "Unknown"),
                "os_version": facts.get("os_version", "Unknown"),
                "serial_number": facts.get("serial_number", "Unknown"),
                "timestamp": datetime.now().isoformat(),
            }
        )
    
    except Exception as e:
        logger.error(f"Failed to collect health from {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "reachable": False,
                "status": "UNREACHABLE",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            },
            failed=True,
        )


def format_text_report(results):
    """Format results as human-readable text."""
    lines = ["=" * 70, "DEVICE HEALTH REPORT", "=" * 70]
    
    healthy_count = unreachable_count = warning_count = critical_count = 0
    
    for host_name, multi_result in sorted(results.items()):
        data = multi_result[0].result
        status = data.get("status", "UNKNOWN")
        
        if status == "HEALTHY":
            healthy_count += 1
            symbol = "✓"
        elif status == "CRITICAL":
            critical_count += 1
            symbol = "✗"
        elif status == "WARNING":
            warning_count += 1
            symbol = "⚠"
        else:
            unreachable_count += 1
            symbol = "○"
        
        lines.append(f"\n{symbol} {host_name}: {status}")
        
        if data.get("reachable"):
            lines.append(f"  Model: {data.get('model')}")
            lines.append(f"  OS: {data.get('os_version')}")
            lines.append(f"  Uptime: {data.get('uptime_seconds')} seconds")
        else:
            lines.append(f"  Error: {data.get('error')}")
    
    lines.extend([
        "\n" + "=" * 70,
        f"Summary: {healthy_count} healthy, {warning_count} warning, "
        f"{critical_count} critical, {unreachable_count} unreachable",
    ])
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Collect device health and status metrics"
    )
    parser.add_argument(
        "--devices",
        default="all",
        help="Device or group filter (default: all)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--output-file",
        help="Write output to file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    logger.info("Initializing Nornir...")
    nr = InitNornir(config_file="config.yaml")
    
    if args.devices != "all":
        nr = nr.filter(F(groups__contains=args.devices) | F(name=args.devices))
    
    logger.info(f"Collecting health from {len(nr.inventory.hosts)} device(s)...")
    results = nr.run(task=collect_health)
    
    if args.output == "json":
        output = json.dumps(
            {h: r[0].result for h, r in results.items()},
            indent=2
        )
    else:
        output = format_text_report(results)
    
    print(output)
    
    if args.output_file:
        with open(args.output_file, "w") as f:
            f.write(output)
        logger.info(f"Output written to {args.output_file}")


if __name__ == "__main__":
    main()
```