```python
"""
Device Health Monitoring Script

Monitor network device health metrics including uptime, system info, and
resource utilization. Uses NAPALM to retrieve device facts and environment
data, evaluates against configurable thresholds, and generates health reports.

Usage:
    python device_health_monitor.py --devices ios1,ios2
    python device_health_monitor.py --threshold 85 --verbose

Prerequisites:
    - nornir >= 3.0
    - napalm
    - SSH access to network devices
    - hosts.yaml with device inventory configured
"""

import logging
import argparse
from typing import Dict
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_device_health(task, threshold: int = 80) -> Dict:
    """
    Retrieve device health metrics via NAPALM.
    
    Collects device facts and environment data, checks CPU/memory utilization
    against threshold, and identifies potential issues.
    
    Args:
        task: Nornir task object
        threshold: Warning threshold for utilization percentage
    
    Returns:
        Dictionary with health status and metrics
    """
    result = {
        "device": task.host.name,
        "status": "ok",
        "alerts": [],
        "metrics": {}
    }
    
    try:
        # Retrieve device facts
        facts_task = task.run(task=napalm_get, getters=["get_facts"])
        facts = facts_task[0].result.get("get_facts", {})
        
        # Store basic device information
        result["metrics"].update({
            "uptime_days": facts.get("uptime", 0) // 86400,
            "model": facts.get("model", "Unknown"),
            "os_version": facts.get("os_version", "Unknown"),
            "serial": facts.get("serial_number", "N/A")
        })
        
        # Retrieve environment data (CPU, memory, temperature)
        try:
            env_task = task.run(task=napalm_get, getters=["get_environment"])
            env = env_task[0].result.get("get_environment", {})
            
            # Evaluate CPU usage
            if "cpu" in env and env["cpu"]:
                cpu_key = list(env["cpu"].keys())[0]
                cpu_usage = env["cpu"][cpu_key].get("%usage", 0)
                result["metrics"]["cpu_usage"] = cpu_usage
                
                if cpu_usage > threshold:
                    result["status"] = "warning"
                    result["alerts"].append(
                        f"CPU usage {cpu_usage}% exceeds {threshold}%"
                    )
            
            # Evaluate memory usage
            if "memory" in env:
                mem_data = env["memory"]
                if "used_ram" in mem_data:
                    result["metrics"]["memory_mb"] = mem_data["used_ram"]
            
            # Check temperature
            if "temperature" in env:
                temps = env["temperature"]
                for sensor, data in temps.items():
                    if data.get("is_alert"):
                        result["status"] = "warning"
                        result["alerts"].append(f"Temperature alert: {sensor}")
        
        except Exception as e:
            logger.debug(f"Environment data unavailable for {task.host.name}: {e}")
    
    except Exception as e:
        logger.error(f"Error retrieving health data for {task.host.name}: {e}")
        result["status"] = "error"
        result["alerts"] = [str(e)]
    
    return result


def format_report(results: Dict) -> int:
    """
    Print formatted health report.
    
    Args:
        results: Results from nornir task execution
    
    Returns:
        Exit code (0 for success, 1 if warnings/errors found)
    """
    print("\n" + "=" * 80)
    print("DEVICE HEALTH REPORT")
    print("=" * 80)
    
    stats = {"ok": 0, "warning": 0, "error": 0}
    
    for host, task_results in results.items():
        if not isinstance(task_results, list) or not task_results:
            continue
        
        health = task_results[0]
        status = health.get("status", "unknown")
        stats[status] = stats.get(status, 0) + 1
        
        # Status icon
        icons = {"ok": "✓", "warning": "⚠", "error": "✗"}
        icon = icons.get(status, "?")
        
        print(f"\n{icon} {health['device']} [{status.upper()}]")
        
        # Print metrics
        if health.get("metrics"):
            print("  Metrics:")
            for key, value in health["metrics"].items():
                print(f"    {key}: {value}")
        
        # Print alerts
        if health.get("alerts"):
            print("  Alerts:")
            for alert in health["alerts"]:
                print(f"    ! {alert}")
    
    print("\n" + "=" * 80)
    print(f"Summary: {stats['ok']} ok, {stats['warning']} warning, {stats['error']} error")
    print("=" * 80 + "\n")
    
    return 0 if (stats["warning"] + stats["error"]) == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="Monitor network device health and resource utilization"
    )
    parser.add_argument(
        "--devices",
        default="all",
        help="Comma-separated device names or 'all' (default: all)"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=80,
        help="CPU/memory warning threshold in percent (default: 80)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # Initialize Nornir with config file
        nr = InitNornir(config_file="config.yaml")
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices from inventory")
        
        # Filter devices if specified
        if args.devices != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} devices")
        
        if not nr.inventory.hosts:
            logger.error("No devices matched the filter criteria")
            return 1
        
        # Execute health checks
        logger.info(f"Starting health checks on {len(nr.inventory.hosts)} devices...")
        results = nr.run(task=get_device_health, threshold=args.threshold)
        
        # Generate report and return appropriate exit code
        return format_report(results)
    
    except FileNotFoundError:
        logger.error("config.yaml not found. Create one with Nornir inventory configuration.")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```