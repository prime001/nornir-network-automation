```python
"""
Device Health Check and Status Report

Purpose:
    Collects and reports health metrics from network devices including uptime,
    CPU usage, memory utilization, and temperature data. Provides quick health
    assessment before maintenance or for ongoing network monitoring.

Usage:
    python device_health.py --hosts all
    python device_health.py --hosts leaf01,leaf02
    python device_health.py --inventory custom_inv.yaml --csv health_report.csv

Prerequisites:
    - Nornir installed with NAPALM drivers
    - Network devices reachable via SSH
    - Credentials configured in inventory.yaml or environment variables
"""

import logging
import argparse
import csv
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.filter import F
from nornir_utils.plugins.functions import print_result
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_health_metrics(task) -> Dict[str, Any]:
    """Gather device health metrics using NAPALM."""
    health = {
        "hostname": task.host.name,
        "reachable": False,
        "model": "N/A",
        "os_version": "N/A",
        "uptime_days": 0,
        "cpu_percent": 0,
        "memory_percent": 0,
    }
    
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        facts = facts_result[0].result.get("facts", {})
        
        health.update({
            "reachable": True,
            "model": facts.get("model", "Unknown"),
            "os_version": facts.get("os_version", "Unknown"),
            "serial_number": facts.get("serial_number", "N/A"),
            "uptime_days": facts.get("uptime_seconds", 0) // 86400,
        })
        
        try:
            env_result = task.run(napalm_get, getters=["environment"])
            env = env_result[0].result.get("environment", {})
            
            if env.get("cpu") and len(env["cpu"]) > 0:
                health["cpu_percent"] = env["cpu"][0].get("cpu_utilization", 0)
            
            if env.get("memory"):
                health["memory_percent"] = env["memory"].get("used_ram", 0)
        except Exception as e:
            logger.debug(f"Could not retrieve environment data for {task.host.name}: {e}")
    
    except Exception as e:
        logger.error(f"Failed to check {task.host.name}: {e}")
        health["error"] = str(e)
    
    return health


def export_to_csv(results, filename: str):
    """Export health check results to CSV file."""
    try:
        fieldnames = ["hostname", "reachable", "model", "os_version", 
                      "uptime_days", "cpu_percent", "memory_percent", "error"]
        
        with open(filename, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for host, task_results in results.items():
                if task_results[0].result:
                    row = {k: task_results[0].result.get(k, "") for k in fieldnames}
                    writer.writerow(row)
        
        logger.info(f"Results exported to {filename}")
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Check health status of network devices"
    )
    parser.add_argument(
        "--hosts",
        default="all",
        help="Comma-separated host list or 'all' (default: all)"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "--csv",
        help="Export results to CSV file"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")
        
        if args.hosts.lower() != "all":
            host_list = [h.strip() for h in args.hosts.split(",")]
            nr = nr.filter(F(name__in=host_list))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} specified hosts")
        
        results = nr.run(task=gather_health_metrics, num_workers=args.workers)
        print_result(results)
        
        if args.csv:
            export_to_csv(results, args.csv)
        
        logger.info("Health check completed")
        return 0
    
    except FileNotFoundError as e:
        logger.error(f"Inventory file not found: {e}")
        return 1
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```