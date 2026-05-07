```python
"""
Device Health Monitor

Collects and analyzes health metrics (CPU, memory, temperature, uptime) from
network devices using NAPALM. Generates reports and alerts when thresholds
are exceeded.

Usage:
    python 031_device_health_monitor.py --inventory inventory.yml --warn-cpu 75

Prerequisites:
    - nornir configured with NAPALM driver
    - Device SSH connectivity
    - YAML inventory file with device groups
"""

import argparse
import logging
from pathlib import Path
from typing import Dict, Any, List

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def collect_health_metrics(task: Task) -> Result:
    """Gather device facts and environment data via NAPALM."""
    return task.run(
        napalm_get,
        getters=["facts", "environment"],
        severity_level=logging.WARNING,
    )


def analyze_health(
    device: str,
    data: Dict[str, Any],
    warn_cpu: int,
    crit_cpu: int,
    warn_mem: int,
    crit_mem: int,
) -> Dict[str, Any]:
    """Analyze health metrics against thresholds."""
    analysis = {"device": device, "status": "healthy", "alerts": [], "metrics": {}}
    
    try:
        facts = data.get("facts", {})
        env = data.get("environment", {})
        
        # Uptime
        uptime_sec = facts.get("uptime", 0)
        analysis["metrics"]["uptime_days"] = round(uptime_sec / 86400, 2)
        
        # Temperature checks
        if "temperature" in env:
            for sensor, info in env["temperature"].items():
                if isinstance(info, dict) and info.get("is_alert"):
                    analysis["status"] = "warning"
                    analysis["alerts"].append(
                        f"Temperature alert: {sensor} at {info.get('current_temperature')}°C"
                    )
        
        # CPU checks
        if "cpu" in env:
            for cpu_name, util_dict in env["cpu"].items():
                if isinstance(util_dict, dict):
                    util = util_dict.get("%usage", 0)
                    analysis["metrics"][f"cpu_{cpu_name}"] = util
                    
                    if util >= crit_cpu:
                        analysis["status"] = "critical"
                        analysis["alerts"].append(f"CPU {cpu_name} critical: {util}%")
                    elif util >= warn_cpu and analysis["status"] != "critical":
                        analysis["status"] = "warning"
                        analysis["alerts"].append(f"CPU {cpu_name} warning: {util}%")
        
        # Memory checks
        if "memory" in env:
            mem_dict = env["memory"]
            if isinstance(mem_dict, dict):
                used = mem_dict.get("used_ram", 0)
                total = mem_dict.get("total_ram", 1)
                mem_pct = (used / total * 100) if total > 0 else 0
                analysis["metrics"]["memory_used_pct"] = round(mem_pct, 1)
                
                if mem_pct >= crit_mem:
                    analysis["status"] = "critical"
                    analysis["alerts"].append(f"Memory critical: {mem_pct:.1f}%")
                elif mem_pct >= warn_mem and analysis["status"] != "critical":
                    analysis["status"] = "warning"
                    analysis["alerts"].append(f"Memory warning: {mem_pct:.1f}%")
    
    except (KeyError, TypeError, ZeroDivisionError) as e:
        logger.warning(f"Error parsing data for {device}: {e}")
        analysis["status"] = "unknown"
    
    return analysis


def generate_report(analyses: List[Dict[str, Any]], output_file: str = None) -> str:
    """Generate formatted health report."""
    report = "=" * 75 + "\n" + "DEVICE HEALTH REPORT\n" + "=" * 75 + "\n\n"
    
    counts = {"healthy": 0, "warning": 0, "critical": 0, "unknown": 0}
    
    for a in analyses:
        counts[a["status"]] += 1
        symbol = "✓" if a["status"] == "healthy" else "⚠" if a["status"] == "warning" else "✗"
        
        report += f"{symbol} {a['device']:<20} [{a['status'].upper()}]\n"
        for alert in a["alerts"]:
            report += f"    {alert}\n"
        if a["metrics"]:
            report += f"    Uptime: {a['metrics'].get('uptime_days', 0):.1f} days\n"
        report += "\n"
    
    report += "=" * 75 + "\n"
    report += f"Summary: {counts['healthy']} healthy, {counts['warning']} warning, "
    report += f"{counts['critical']} critical, {counts['unknown']} unknown\n"
    report += "=" * 75 + "\n"
    
    if output_file:
        Path(output_file).write_text(report)
        logger.info(f"Report saved to {output_file}")
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Monitor device health metrics across network infrastructure"
    )
    parser.add_argument(
        "-i", "--inventory", default="inventory.yml", help="Inventory file path"
    )
    parser.add_argument("-f", "--filter", help="Filter devices by name pattern")
    parser.add_argument("--warn-cpu", type=int, default=75, help="CPU warning %%")
    parser.add_argument("--crit-cpu", type=int, default=90, help="CPU critical %%")
    parser.add_argument("--warn-mem", type=int, default=80, help="Memory warning %%")
    parser.add_argument("--crit-mem", type=int, default=95, help="Memory critical %%")
    parser.add_argument("-o", "--output", help="Output file for report")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        if args.filter:
            nr = nr.filter(name=args.filter)
        
        if not nr.inventory.hosts:
            logger.error(f"No devices match filter: {args.filter}")
            return 1
        
        logger.info(f"Collecting health from {len(nr.inventory.hosts)} devices...")
        results = nr.run(task=collect_health_metrics)
        
        analyses = []
        for device_name, task_result in results.items():
            if task_result[0].failed:
                logger.error(f"Failed to collect from {device_name}")
                analyses.append({
                    "device": device_name,
                    "status": "unknown",
                    "alerts": ["Collection failed"],
                    "metrics": {},
                })
            else:
                analysis = analyze_health(
                    device_name,
                    task_result[0].result,
                    args.warn_cpu,
                    args.crit_cpu,
                    args.warn_mem,
                    args.crit_mem,
                )
                analyses.append(analysis)
        
        report = generate_report(analyses, args.output)
        print(report)
        
        critical_count = sum(1 for a in analyses if a["status"] == "critical")
        warning_count = sum(1 for a in analyses if a["status"] == "warning")
        
        return 2 if critical_count > 0 else (1 if warning_count > 0 else 0)
    
    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```