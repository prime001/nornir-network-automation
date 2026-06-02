```python
"""
Device Health Monitor - Network Device Status and Reachability Auditor

Purpose:
    Monitor network device health including reachability, uptime, OS versions,
    and basic system metrics. Generates a health report with device status summary.

Usage:
    python device_health_monitor.py --inventory inventory.yaml --group core
    python device_health_monitor.py --inventory inventory.yaml --device router1
    python device_health_monitor.py --inventory inventory.yaml --output health_report.json

Prerequisites:
    - Nornir installed (pip install nornir)
    - Network inventory file (inventory.yaml or YAML format)
    - Device credentials in inventory or environment
    - napalm plugin for Nornir (pip install napalm)

Architecture:
    - Uses Nornir for parallel device connectivity
    - Collects device facts via NAPALM get_facts
    - Generates health report with reachability and system info
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(verbose: bool) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def get_device_facts(task: Task) -> Result:
    """
    Gather device facts using NAPALM.
    
    Args:
        task: Nornir task context
        
    Returns:
        Result with device facts dict
    """
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result.get("facts", {})
        return Result(
            host=task.host,
            result={
                "status": "reachable",
                "hostname": facts.get("hostname", "unknown"),
                "os_version": facts.get("os_version", "unknown"),
                "serial_number": facts.get("serial_number", "unknown"),
                "uptime_seconds": facts.get("uptime_seconds", -1),
                "vendor": facts.get("vendor", "unknown"),
                "model": facts.get("model", "unknown"),
            }
        )
    except Exception as e:
        logging.error(f"Failed to gather facts from {task.host}: {str(e)}")
        return Result(
            host=task.host,
            result={
                "status": "unreachable",
                "error": str(e),
            },
            failed=True
        )


def generate_health_report(task_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate health summary report from task results.
    
    Args:
        task_results: Dictionary of host results
        
    Returns:
        Health report dict with summary and per-device details
    """
    reachable_count = 0
    unreachable_count = 0
    devices = []
    
    for host_name, host_data in task_results.items():
        if "get_device_facts" in host_data:
            host_result = host_data["get_device_facts"][0].result
            status = host_result.get("status", "unknown")
            
            if status == "reachable":
                reachable_count += 1
            else:
                unreachable_count += 1
            
            devices.append({
                "hostname": host_name,
                "status": status,
                "data": host_result
            })
    
    total = len(devices)
    return {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_devices": total,
            "reachable": reachable_count,
            "unreachable": unreachable_count,
            "health_percentage": (
                (reachable_count / total * 100) if total > 0 else 0
            )
        },
        "devices": sorted(devices, key=lambda x: x["hostname"])
    }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Monitor network device health and reachability"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "--group",
        help="Filter devices by group name"
    )
    parser.add_argument(
        "--device",
        help="Target single device by hostname"
    )
    parser.add_argument(
        "--output",
        help="Write JSON report to file (default: stdout)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")
        
        if args.device:
            nr = nr.filter(name=args.device)
            logger.info(f"Filtered to device: {args.device}")
        elif args.group:
            nr = nr.filter(group=args.group)
            logger.info(f"Filtered to group: {args.group}")
        
        if not nr.inventory.hosts:
            logger.error("No devices selected after filtering")
            return 1
        
        logger.info(f"Gathering facts from {len(nr.inventory.hosts)} devices")
        results = nr.run(task=get_device_facts)
        
        report = generate_health_report(results.host_results)
        
        output_json = json.dumps(report, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output_json)
            logger.info(f"Report written to {args.output}")
        else:
            print(output_json)
        
        summary = report["summary"]
        logger.info(
            f"Health Summary: {summary['reachable']}/{summary['total_devices']} "
            f"reachable ({summary['health_percentage']:.1f}%)"
        )
        
        return 0 if summary["unreachable"] == 0 else 1
        
    except FileNotFoundError as e:
        logger.error(f"Inventory file not found: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```