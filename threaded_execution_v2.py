```python
"""
Device Health Check Script

Purpose:
    Gathers device facts and validates health status using NAPALM.
    Reports uptime, OS version, and basic resource metrics.

Usage:
    python device_health_check.py --device router1 --username admin --password secret
    python device_health_check.py --group core --username admin --password secret
    python device_health_check.py --all --username admin --password secret

Prerequisites:
    - Nornir installed and configured with inventory
    - NAPALM drivers installed for target device types
    - Network connectivity to target devices
    - Valid credentials with read access
"""

import argparse
import logging
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def validate_health(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Validate device health based on gathered facts."""
    health = {
        "status": "healthy",
        "checks": {},
        "warnings": [],
    }
    
    uptime = facts.get("uptime", 0)
    if uptime < 3600:
        health["warnings"].append(f"Device uptime only {uptime} seconds")
    else:
        health["checks"]["uptime"] = "OK"
    
    if facts.get("vendor", "").lower() == "unknown":
        health["status"] = "degraded"
        health["warnings"].append("Unknown vendor detected")
    
    health["checks"]["vendor"] = facts.get("vendor", "N/A")
    health["checks"]["model"] = facts.get("model", "N/A")
    health["checks"]["os_version"] = facts.get("os_version", "N/A")
    health["checks"]["serial_number"] = facts.get("serial_number", "N/A")
    
    return health


def check_device_health(task: Task) -> Result:
    """Main task to check device health."""
    try:
        r = task.run(napalm_get, getters=["facts"])
        
        if r.failed:
            return Result(
                host=task.host,
                failed=True,
                result="Failed to gather device facts"
            )
        
        facts = r[0].result["facts"]
        health = validate_health(facts)
        
        output = {
            "hostname": task.host.name,
            "address": task.host.host,
            "facts": facts,
            "health": health,
        }
        
        return Result(host=task.host, result=output)
    
    except Exception as e:
        logger.error(f"Error checking health for {task.host}: {e}")
        return Result(
            host=task.host,
            failed=True,
            result=f"Exception: {str(e)}"
        )


def print_health_report(results: Dict) -> None:
    """Print formatted health report from results."""
    print("\n" + "=" * 80)
    print("DEVICE HEALTH REPORT")
    print("=" * 80)
    
    for host_name, multi_result in results.items():
        if multi_result[0].failed:
            print(f"\n[FAILED] {host_name}: {multi_result[0].result}")
            continue
        
        data = multi_result[0].result
        facts = data["facts"]
        health = data["health"]
        
        uptime_hours = facts.get("uptime", 0) // 3600
        print(f"\n{host_name} ({data['address']})")
        print("-" * 80)
        print(f"  Vendor: {facts.get('vendor', 'N/A')}")
        print(f"  Model: {facts.get('model', 'N/A')}")
        print(f"  OS Version: {facts.get('os_version', 'N/A')}")
        print(f"  Serial: {facts.get('serial_number', 'N/A')}")
        print(f"  Uptime: {uptime_hours} hours")
        print(f"  Status: {health['status'].upper()}")
        
        if health["warnings"]:
            print(f"  Warnings:")
            for warning in health["warnings"]:
                print(f"    - {warning}")
    
    print("\n" + "=" * 80)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check device health and gather facts using NAPALM"
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Target specific device by hostname"
    )
    parser.add_argument(
        "--group",
        type=str,
        help="Target specific group"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Target all devices in inventory"
    )
    parser.add_argument(
        "--username",
        type=str,
        required=True,
        help="Username for device authentication"
    )
    parser.add_argument(
        "--password",
        type=str,
        required=True,
        help="Password for device authentication"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir(config_file="config.yaml")
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return
    
    nr.inventory.defaults.username = args.username
    nr.inventory.defaults.password = args.password
    
    if args.device:
        nr = nr.filter(name=args.device)
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))
    elif not args.all:
        logger.warning("No device filter specified. Use --device, --group, or --all")
        return
    
    if not nr.inventory.hosts:
        logger.error("No devices matched filter criteria")
        return
    
    logger.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")
    
    results = nr.run(task=check_device_health)
    
    print_health_report(results)
    
    failed = sum(1 for r in results.values() if r[0].failed)
    total = len(results)
    logger.info(f"Health check complete: {total - failed}/{total} successful")


if __name__ == "__main__":
    main()
```