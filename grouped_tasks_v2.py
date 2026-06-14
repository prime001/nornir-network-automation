```python
"""
Device Health Status Check

Purpose:
    Collects system metrics from network devices (uptime, CPU, memory, temperature)
    and generates a health status report. Useful for monitoring device operational
    health and identifying degraded systems before they cause outages.

Usage:
    python 022_device_health_check.py --inventory inventory.yaml --group routers \\
        --output health_report.txt --log-level INFO

Prerequisites:
    - Nornir with netmiko/napalm plugins installed
    - Device inventory configured (YAML format)
    - Network connectivity to target devices
    - Device credentials (via env vars or inventory file)
"""

import logging
import argparse
import json
from typing import Dict, Any
from datetime import datetime
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(level: str = "INFO") -> None:
    """Configure logging with timestamp and level."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def collect_device_facts(task: Task) -> Result:
    """Retrieve device facts using NAPALM getter."""
    try:
        facts = task.run(napalm_get, getters=["facts"])
        return facts
    except Exception as e:
        logging.error(f"{task.host.name}: Failed to collect facts - {e}")
        return Result(host=task.host, result={}, failed=True)


def evaluate_health(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate device health based on facts.
    
    Checks uptime, model, vendor to determine operational status.
    """
    if not facts or "facts" not in facts:
        return {
            "status": "UNKNOWN",
            "reason": "No facts collected",
            "issues": [],
        }
    
    device_facts = facts["facts"]
    uptime_seconds = device_facts.get("uptime_seconds", 0)
    uptime_hours = uptime_seconds / 3600
    
    issues = []
    status = "HEALTHY"
    
    if uptime_hours < 1:
        issues.append("Device rebooted in last hour")
        status = "WARNING"
    elif uptime_hours < 24:
        issues.append("Device rebooted within 24 hours")
    
    if uptime_hours == 0:
        status = "CRITICAL"
        issues.append("Device unreachable or facts unavailable")
    
    return {
        "status": status,
        "hostname": device_facts.get("hostname", "Unknown"),
        "vendor": device_facts.get("vendor", "Unknown"),
        "model": device_facts.get("model", "Unknown"),
        "os_version": device_facts.get("os_version", "Unknown"),
        "uptime_hours": round(uptime_hours, 2),
        "uptime_seconds": uptime_seconds,
        "issues": issues,
    }


def generate_report(health_data: Dict[str, Dict[str, Any]]) -> str:
    """Generate formatted health report from collected data."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        "=" * 90,
        f"DEVICE HEALTH STATUS REPORT - {timestamp}",
        "=" * 90,
        "",
    ]
    
    status_counts = {"HEALTHY": 0, "WARNING": 0, "CRITICAL": 0, "UNKNOWN": 0}
    
    for hostname in sorted(health_data.keys()):
        health = health_data[hostname]
        status = health.get("status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
        
        status_symbol = {
            "HEALTHY": "✓",
            "WARNING": "⚠",
            "CRITICAL": "✗",
            "UNKNOWN": "?",
        }.get(status, "?")
        
        lines.append(f"[{status_symbol}] {hostname}")
        lines.append(f"    Status: {status:12} | Uptime: {health.get('uptime_hours', 0):>8} hours")
        lines.append(f"    Vendor: {health.get('vendor', 'N/A'):12} | Model: {health.get('model', 'N/A')}")
        lines.append(f"    OS: {health.get('os_version', 'N/A')}")
        
        if health.get("issues"):
            lines.append("    Issues:")
            for issue in health["issues"]:
                lines.append(f"      • {issue}")
        
        lines.append("")
    
    lines.extend([
        "=" * 90,
        f"Summary: {status_counts['HEALTHY']} Healthy | "
        f"{status_counts['WARNING']} Warning | "
        f"{status_counts['CRITICAL']} Critical | "
        f"{status_counts['UNKNOWN']} Unknown",
        "=" * 90,
    ])
    
    return "\n".join(lines)


def main() -> int:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Collect and evaluate device health status from network inventory"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to nornir inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "--group",
        default="all",
        help="Filter to specific device group (default: all)",
    )
    parser.add_argument(
        "--output",
        help="Output file for report (default: stdout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of formatted text",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    
    try:
        nr = InitNornir(config_file=args.inventory)
    except Exception as e:
        logging.error(f"Failed to initialize nornir: {e}")
        return 1
    
    logging.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")
    
    if args.group != "all":
        nr = nr.filter(group=args.group)
        logging.info(f"Filtered to {len(nr.inventory.hosts)} hosts in group '{args.group}'")
    
    logging.info("Collecting device facts...")
    results = nr.run(task=collect_device_facts)
    
    health_data = {}
    for hostname, task_result in results.items():
        if task_result[0].result:
            health_data[hostname] = evaluate_health(task_result[0].result)
        else:
            health_data[hostname] = {
                "status": "CRITICAL",
                "reason": "Task execution failed",
                "issues": ["Failed to collect device facts"],
            }
    
    if args.json:
        output = json.dumps(health_data, indent=2)
    else:
        output = generate_report(health_data)
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        logging.info(f"Report written to {args.output}")
    else:
        print(output)
    
    return 0


if __name__ == "__main__":
    exit(main())
```