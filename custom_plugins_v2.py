```python
"""
Device Health Monitoring Script

Purpose: Collect and analyze device health metrics including CPU, memory, disk usage,
uptime, and environmental sensors.

Usage:
    python device_health_monitor.py --hosts router1,router2 --username admin --password secret
    python device_health_monitor.py --device router1 --username admin --password secret

Prerequisites:
    - nornir library installed
    - napalm driver installed for target device types
    - Network device SSH access
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_device_health(task):
    """Retrieve device health metrics using NAPALM."""
    try:
        facts_result = task.run(
            napalm_get,
            getters=["facts", "environment"]
        )
        
        facts = facts_result[0].result
        env = facts.get("environment", {})
        dev_facts = facts.get("facts", {})
        
        return {
            "hostname": task.host.name,
            "vendor": dev_facts.get("vendor", "N/A"),
            "model": dev_facts.get("model", "N/A"),
            "os_version": dev_facts.get("os_version", "N/A"),
            "uptime_seconds": dev_facts.get("uptime_seconds", 0),
            "cpu_percent": env.get("cpu", [{}])[0].get("%usage", "N/A"),
            "memory_used": env.get("memory", {}).get("used_ram", "N/A"),
            "memory_available": env.get("memory", {}).get("available_ram", "N/A"),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error gathering health data for {task.host.name}: {e}")
        return {"hostname": task.host.name, "error": str(e)}


def format_uptime(seconds):
    """Convert uptime seconds to human-readable format."""
    if not isinstance(seconds, int):
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def print_report(results):
    """Display health metrics in formatted table."""
    print("\n" + "=" * 110)
    print(f"{'Device':<20} {'Vendor':<12} {'Model':<20} {'OS Version':<15} {'Uptime':<15} {'CPU %':<10}")
    print("=" * 110)
    
    for host, task_result in results.items():
        if task_result[0].failed:
            print(f"{host:<20} ERROR: {task_result[0].exception}")
        else:
            health = task_result[0].result
            if "error" in health:
                print(f"{health['hostname']:<20} ERROR: {health['error']}")
            else:
                uptime = format_uptime(health.get("uptime_seconds", 0))
                cpu = str(health.get("cpu_percent", "N/A"))
                print(
                    f"{health['hostname']:<20} {health.get('vendor', 'N/A'):<12} "
                    f"{health.get('model', 'N/A'):<20} {health.get('os_version', 'N/A'):<15} "
                    f"{uptime:<15} {cpu:<10}"
                )
    print("=" * 110 + "\n")


def export_csv(results, output_file):
    """Export results to CSV file."""
    try:
        with open(output_file, "w", newline="") as csvfile:
            fieldnames = [
                "hostname", "vendor", "model", "os_version", "uptime_seconds",
                "cpu_percent", "memory_used", "memory_available", "timestamp"
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for host, task_result in results.items():
                if not task_result[0].failed and "error" not in task_result[0].result:
                    writer.writerow(task_result[0].result)
        
        logger.info(f"Results exported to {output_file}")
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")


def main():
    parser = argparse.ArgumentParser(description="Monitor network device health metrics")
    parser.add_argument("--hosts", help="Comma-separated device list or 'all'")
    parser.add_argument("--device", help="Single device hostname")
    parser.add_argument("--username", required=True, help="SSH username")
    parser.add_argument("--password", required=True, help="SSH password")
    parser.add_argument("--inventory", default="hosts.yaml", help="Inventory file path")
    parser.add_argument("--output", help="CSV output file path")
    parser.add_argument("--timeout", type=int, default=30, help="Connection timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if not args.hosts and not args.device:
        logger.error("Must specify --hosts or --device")
        sys.exit(1)
    
    if not Path(args.inventory).exists():
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(F(name=args.device))
        elif args.hosts and args.hosts != "all":
            host_list = [h.strip() for h in args.hosts.split(",")]
            nr = nr.filter(F(name__any=host_list))
        
        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password
            if "napalm" in host.connection_options:
                host.connection_options["napalm"].timeout = args.timeout
        
        logger.info(f"Gathering health data from {len(nr.inventory.hosts)} devices...")
        results = nr.run(task=get_device_health)
        
        print_report(results)
        
        if args.output:
            export_csv(results, args.output)
        
        logger.info("Health monitoring completed")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
```