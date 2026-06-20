```python
#!/usr/bin/env python3
"""
Device Reachability and Interface Health Check

Purpose:
    Validates device connectivity and gathers interface statistics to identify
    network health issues. Detects unreachable devices, interface errors, and
    link problems across a network inventory.

Usage:
    python device_health_check.py --inventory inventory.yaml --device 192.168.1.1
    python device_health_check.py --inventory inventory.yaml --device all --format json
    python device_health_check.py --inventory inventory.yaml --threshold 20

Prerequisites:
    - nornir, nornir-napalm, and netaddr installed
    - Valid inventory file with device credentials
    - Network connectivity to target devices
    - NAPALM support for device OS platforms
"""

import argparse
import json
import logging
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def gather_interface_health(task: Any, error_threshold: int = 10) -> Dict[str, Any]:
    """
    Collect interface statistics and identify problematic interfaces.
    
    Args:
        task: Nornir task object
        error_threshold: Error/discard count threshold for alerting
    
    Returns:
        Dictionary containing device health metrics
    """
    health_data = {
        "device": task.host.name,
        "reachable": False,
        "status": "unreachable",
        "total_interfaces": 0,
        "problem_count": 0,
        "problems": [],
    }
    
    try:
        result = task.run(
            napalm_get,
            getters=["interfaces", "interfaces_counters"],
            name="gather_stats",
        )
        
        interfaces = result[0].result.get("interfaces", {})
        counters = result[0].result.get("interfaces_counters", {})
        
        health_data["reachable"] = True
        health_data["total_interfaces"] = len(interfaces)
        
        for iface_name, iface_info in interfaces.items():
            iface_counts = counters.get(iface_name, {})
            
            error_count = (
                iface_counts.get("rx_errors", 0) +
                iface_counts.get("tx_errors", 0)
            )
            discard_count = (
                iface_counts.get("rx_discards", 0) +
                iface_counts.get("tx_discards", 0)
            )
            
            has_problems = (
                not iface_info.get("is_up") or
                error_count > error_threshold or
                discard_count > error_threshold
            )
            
            if has_problems:
                health_data["problems"].append({
                    "interface": iface_name,
                    "is_up": iface_info.get("is_up"),
                    "errors": error_count,
                    "discards": discard_count,
                    "speed": iface_info.get("speed"),
                })
                health_data["problem_count"] += 1
        
        health_data["status"] = "healthy" if health_data["problem_count"] == 0 else "degraded"
        
    except Exception as e:
        logger.error(f"Error on {task.host.name}: {str(e)}")
        health_data["error"] = str(e)
    
    return health_data


def format_table_output(results: list) -> None:
    print("\n" + "=" * 90)
    print(f"{'Device':<20} {'Status':<15} {'Reachable':<12} {'Interfaces':<12} {'Problems':<12}")
    print("=" * 90)
    
    for device in results:
        status = device.get("status", "unknown")
        reachable = "Yes" if device.get("reachable") else "No"
        total = device.get("total_interfaces", 0)
        problems = device.get("problem_count", 0)
        
        print(f"{device['device']:<20} {status:<15} {reachable:<12} {total:<12} {problems:<12}")
        
        if device.get("problems"):
            for prob in device["problems"]:
                status_str = "UP" if prob["is_up"] else "DOWN"
                print(f"  └─ {prob['interface']}: {status_str} "
                      f"(errors: {prob['errors']}, discards: {prob['discards']})")
    
    print("=" * 90)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check device health and interface status across network"
    )
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to nornir inventory configuration file",
    )
    parser.add_argument(
        "--device",
        default="all",
        help="Target device name or 'all'",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Error/discard count threshold for alerting",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device != "all":
            nr = nr.filter(F(name=args.device))
        
        if not nr.inventory.hosts:
            logger.error(f"No devices matching filter: {args.device}")
            return
        
        logger.info(f"Scanning {len(nr.inventory.hosts)} device(s)...")
        
        results = nr.run(
            task=gather_interface_health,
            error_threshold=args.threshold,
        )
        
        health_report = [
            task_results[0].result
            for task_results in results.values()
            if task_results[0].result
        ]
        
        if args.format == "json":
            print(json.dumps(health_report, indent=2))
        else:
            format_table_output(health_report)
            
            degraded_count = sum(1 for d in health_report if d["status"] == "degraded")
            unreachable_count = sum(1 for d in health_report if not d["reachable"])
            
            if degraded_count > 0 or unreachable_count > 0:
                logger.warning(
                    f"Issues detected: {unreachable_count} unreachable, "
                    f"{degraded_count} degraded"
                )
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return


if __name__ == "__main__":
    main()
```