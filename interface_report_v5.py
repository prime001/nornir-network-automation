```python
"""Device Health Status Checker

Purpose:
    Collects health metrics (uptime, CPU, memory, disk usage) from network
    devices and generates a health status report with CSV export capability.

Usage:
    python device_health_checker.py --inventory inventory.yaml
    python device_health_checker.py --devices router1,router2 --output health.csv
    python device_health_checker.py --device-group core --verbose

Prerequisites:
    - Nornir installed (pip install nornir)
    - NAPALM driver installed for your network platform
    - SSH/SNMP access configured on network devices
    - Inventory file in YAML format with device groups and credentials
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from typing import Dict, Any, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def collect_health_metrics(task) -> Dict[str, Any]:
    """
    Collect CPU, memory, disk, and uptime metrics from a device.
    Returns device health data or error details if collection fails.
    """
    try:
        result = task.run(
            name="collect_health",
            task=napalm_get,
            getters=["facts", "environment"]
        )
        
        if not result:
            return {"device": task.host.name, "status": "unreachable", "error": "No data returned"}
        
        data = result[0].result
        facts = data.get("facts", {})
        environment = data.get("environment", {})
        
        cpu_usage = "N/A"
        if "cpu" in environment and len(environment["cpu"]) > 0:
            cpu_usage = environment["cpu"][0].get("%usage", "N/A")
        
        memory_usage = environment.get("memory", {}).get("%usage", "N/A")
        
        return {
            "device": task.host.name,
            "status": "ok",
            "uptime_seconds": facts.get("uptime_seconds", 0),
            "os_version": facts.get("os_version", "N/A"),
            "serial_number": facts.get("serial_number", "N/A"),
            "cpu_usage": cpu_usage,
            "memory_usage": memory_usage,
            "model": facts.get("model", "N/A"),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.warning(f"Health collection failed for {task.host.name}: {str(e)}")
        return {
            "device": task.host.name,
            "status": "unreachable",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


def format_uptime(seconds: int) -> str:
    """Convert uptime from seconds to human-readable format."""
    if not isinstance(seconds, int) or seconds < 0:
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def print_report(results: Dict) -> None:
    """Print formatted health report to console."""
    print("\n" + "=" * 110)
    print(f"{'Device':<20} {'Status':<12} {'Uptime':<18} {'CPU %':<10} {'Memory %':<12} {'Model':<25}")
    print("=" * 110)
    
    for host_data in results:
        if host_data.get("status") == "unreachable":
            print(f"{host_data.get('device', 'unknown'):<20} {'FAIL':<12} {'-':<18} {'-':<10} {'-':<12} {'-':<25}")
        else:
            uptime = format_uptime(host_data.get("uptime_seconds", 0))
            cpu = str(host_data.get("cpu_usage", "N/A"))[:9]
            memory = str(host_data.get("memory_usage", "N/A"))[:11]
            model = host_data.get("model", "N/A")[:24]
            
            print(f"{host_data.get('device'):<20} {'OK':<12} {uptime:<18} {cpu:<10} {memory:<12} {model:<25}")
    
    print("=" * 110 + "\n")


def export_csv(results: Dict, output_file: str) -> None:
    """Export health metrics to CSV file."""
    try:
        with open(output_file, 'w', newline='') as csvfile:
            fieldnames = ["device", "status", "uptime", "cpu_usage", "memory_usage", "model", "serial_number", "os_version", "timestamp"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for host_data in results:
                row = {
                    "device": host_data.get("device"),
                    "status": host_data.get("status"),
                    "uptime": format_uptime(host_data.get("uptime_seconds", 0)) if host_data.get("status") == "ok" else "N/A",
                    "cpu_usage": host_data.get("cpu_usage", "N/A"),
                    "memory_usage": host_data.get("memory_usage", "N/A"),
                    "model": host_data.get("model", "N/A"),
                    "serial_number": host_data.get("serial_number", "N/A"),
                    "os_version": host_data.get("os_version", "N/A"),
                    "timestamp": host_data.get("timestamp")
                }
                writer.writerow(row)
        
        logger.info(f"Health report exported to {output_file}")
    except IOError as e:
        logger.error(f"Failed to write CSV file: {e}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Collect and report device health metrics across network devices"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Nornir inventory file path (default: inventory.yaml)"
    )
    parser.add_argument(
        "--devices",
        help="Comma-separated device names to check"
    )
    parser.add_argument(
        "--device-group",
        help="Filter by device group name"
    )
    parser.add_argument(
        "--output",
        help="Export results to CSV file"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
        elif args.device_group:
            nr = nr.filter(F(groups__contains=args.device_group))
        
        if len(nr.inventory.hosts) == 0:
            logger.error("No devices matched the filter criteria")
            return 1
        
        logger.info(f"Starting health check for {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=collect_health_metrics)
        
        health_data = [results[host][0].result for host in results]
        
        print_report(health_data)
        
        if args.output:
            export_csv(health_data, args.output)
        
        reachable = sum(1 for d in health_data if d.get("status") == "ok")
        unreachable = len(health_data) - reachable
        logger.info(f"Health check complete: {reachable} ok, {unreachable} unreachable")
        
        return 0 if unreachable == 0 else 1
        
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```