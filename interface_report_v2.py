```python
"""
Device Health Check and Monitoring Report

Retrieves CPU, memory, temperature, and uptime metrics from network devices
using NAPALM getters. Generates a formatted health report and alerts on
threshold violations.

Usage:
    python device_health_check.py --inventory inventory.yaml --hosts "router1,router2"
    python device_health_check.py --inventory inventory.yaml --groups "core"
    python device_health_check.py --inventory inventory.yaml --cpu-threshold 80

Prerequisites:
    - nornir and nornir plugins installed
    - NAPALM drivers available for device types
    - Inventory file in YAML/JSON format
    - Network connectivity to all target devices
"""

import argparse
import logging
import json
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_device_health(task: Task, cpu_warn: int, mem_warn: int) -> Result:
    """Collect device health metrics using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["environment", "facts"])
        device_facts = result[1].result
        device_env = result[0].result
        
        health_data = {
            "hostname": task.host.name,
            "device_type": task.host.get("device_type", "unknown"),
            "facts": device_facts.get("facts", {}),
            "environment": device_env.get("environment", {}),
        }
        return Result(host=task.host, result=health_data)
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to retrieve health data - {e}")
        return Result(host=task.host, result=None, failed=True, exception=e)


def analyze_health(health_data: dict, cpu_warn: int, mem_warn: int) -> dict:
    """Analyze health metrics and identify issues."""
    if not health_data:
        return {"status": "ERROR", "message": "No data collected"}
    
    analysis = {
        "hostname": health_data["hostname"],
        "device_type": health_data["device_type"],
        "alerts": [],
        "status": "HEALTHY"
    }
    
    facts = health_data.get("facts", {})
    env = health_data.get("environment", {})
    
    if "uptime_seconds" in facts:
        days = facts["uptime_seconds"] // 86400
        analysis["uptime_days"] = days
        if days < 7:
            analysis["alerts"].append(f"Low uptime: {days} days")
            analysis["status"] = "WARNING"
    
    for cpu_name, cpu_data in env.get("cpu", {}).items():
        if isinstance(cpu_data, dict):
            cpu_util = cpu_data.get("%usage", 0)
            if cpu_util > cpu_warn:
                analysis["alerts"].append(f"High CPU: {cpu_name} = {cpu_util}%")
                analysis["status"] = "CRITICAL" if cpu_util > 95 else "WARNING"
    
    mem = env.get("memory", {})
    if "available_ram" in mem and "used_ram" in mem:
        total = mem["available_ram"] + mem["used_ram"]
        if total > 0:
            mem_util = (mem["used_ram"] / total) * 100
            if mem_util > mem_warn:
                analysis["alerts"].append(f"High memory: {mem_util:.1f}%")
                analysis["status"] = "CRITICAL" if mem_util > 98 else "WARNING"
    
    for sensor_name, sensor_data in env.get("temperature", {}).items():
        if isinstance(sensor_data, dict):
            current_temp = sensor_data.get("current_reading", 0)
            if current_temp > 75:
                analysis["alerts"].append(f"High temp: {sensor_name} = {current_temp}C")
                analysis["status"] = "CRITICAL" if current_temp > 85 else "WARNING"
    
    if not analysis["alerts"]:
        analysis["alerts"] = ["All metrics within normal range"]
    
    return analysis


def print_report(analyses: list) -> None:
    """Format and print health report."""
    print("\n" + "=" * 80)
    print("DEVICE HEALTH REPORT")
    print("=" * 80)
    
    critical = sum(1 for a in analyses if a["status"] == "CRITICAL")
    warning = sum(1 for a in analyses if a["status"] == "WARNING")
    healthy = sum(1 for a in analyses if a["status"] == "HEALTHY")
    
    print(f"\nSummary: {healthy} healthy, {warning} warning, {critical} critical")
    print("-" * 80)
    
    for analysis in sorted(analyses, key=lambda x: x["status"], reverse=True):
        status = analysis["status"]
        marker = "⚠️ " if status == "WARNING" else "🔴" if status == "CRITICAL" else "✓"
        print(f"\n{marker} {analysis['hostname']} ({analysis['device_type']}) - {status}")
        
        if "uptime_days" in analysis:
            print(f"  Uptime: {analysis['uptime_days']} days")
        
        for alert in analysis["alerts"]:
            print(f"  • {alert}")


def main():
    parser = argparse.ArgumentParser(
        description="Check device health metrics (CPU, memory, temperature)"
    )
    parser.add_argument("--inventory", required=True, help="Path to nornir inventory file")
    parser.add_argument("--hosts", help="Comma-separated list of hosts to check")
    parser.add_argument("--groups", help="Comma-separated list of groups to check")
    parser.add_argument("--cpu-threshold", type=int, default=85, help="CPU warning threshold %")
    parser.add_argument("--memory-threshold", type=int, default=90, help="Memory warning threshold %")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.hosts:
            nr = nr.filter(name=args.hosts.split(","))
        elif args.groups:
            nr = nr.filter(group=args.groups.split(","))
        
        logger.info(f"Checking health for {len(nr.inventory.hosts)} devices")
        
        results = nr.run(
            task=get_device_health,
            cpu_warn=args.cpu_threshold,
            mem_warn=args.memory_threshold
        )
        
        analyses = []
        for host_name, multi_result in results.items():
            if multi_result[0].result:
                analysis = analyze_health(
                    multi_result[0].result,
                    args.cpu_threshold,
                    args.memory_threshold
                )
                analyses.append(analysis)
        
        if args.json:
            print(json.dumps(analyses, indent=2))
        else:
            print_report(analyses)
        
        critical_count = sum(1 for a in analyses if a["status"] == "CRITICAL")
        if critical_count > 0:
            logger.warning(f"Found {critical_count} device(s) in CRITICAL state")
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise


if __name__ == "__main__":
    main()
```