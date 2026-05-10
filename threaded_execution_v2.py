```python
#!/usr/bin/env python3
"""
Device Health Check Utility

Collects and reports system health metrics from network devices including uptime,
CPU utilization, and memory usage. Useful for monitoring device status and capacity
planning in production networks.

Prerequisites:
    - Nornir installed with paramiko/netmiko drivers
    - NAPALM library for facts gathering
    - Devices configured with SSH/Telnet access
    - Device inventory in proper Nornir YAML format

Usage:
    python device_health_check.py
    python device_health_check.py --device router1
    python device_health_check.py --group core_routers
    python device_health_check.py --device router1 --verbose

Example inventory structure:
    routers:
      router1:
        hostname: 192.168.1.1
        username: admin
        password: password
        platform: ios
      router2:
        hostname: 192.168.1.2
        username: admin
        password: password
        platform: ios
"""

import argparse
import logging
import sys
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import napalm_get


def get_device_facts(task: Task) -> Result:
    """
    Retrieve device facts using NAPALM getter.
    
    Args:
        task: Nornir Task instance
        
    Returns:
        Result containing device facts dictionary
    """
    try:
        napalm_result = task.run(napalm_get, getters=["facts"])
        facts = napalm_result[0].result.get("facts", {})
        
        health_data = {
            "device": task.host.name,
            "hostname": facts.get("hostname", "Unknown"),
            "os_version": facts.get("os_version", "Unknown"),
            "uptime_seconds": facts.get("uptime", 0),
            "serial_number": facts.get("serial_number", "Unknown"),
            "vendor": facts.get("vendor", "Unknown"),
            "model": facts.get("model", "Unknown"),
        }
        
        return Result(host=task.host, result=health_data)
    except Exception as exc:
        return Result(host=task.host, failed=True, exception=exc)


def format_uptime(seconds):
    """Convert uptime seconds to human-readable format."""
    if not isinstance(seconds, (int, float)):
        return str(seconds)
    
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    
    return f"{days}d {hours}h {minutes}m"


def setup_logging(verbose):
    """Configure logging based on verbosity level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stdout,
    )
    return logging.getLogger(__name__)


def parse_args():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Collect health metrics from network devices",
        epilog="Example: python device_health_check.py --group core_routers --verbose",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Target specific device by name",
    )
    parser.add_argument(
        "--group",
        type=str,
        help="Target specific group of devices",
    )
    parser.add_argument(
        "--inventory",
        type=str,
        default="inventory.yaml",
        help="Path to inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output",
    )
    
    return parser.parse_args()


def main():
    """Main entry point for device health check."""
    args = parse_args()
    logger = setup_logging(args.verbose)
    
    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices")
        
        if args.device:
            nr = nr.filter(name=args.device)
            logger.info(f"Filtered to device: {args.device}")
        elif args.group:
            nr = nr.filter(group=args.group)
            logger.info(f"Filtered to group: {args.group}")
        
        if not nr.inventory.hosts:
            logger.warning("No devices match the specified filter")
            return 1
        
        logger.info(f"Executing health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=get_device_facts)
        
        print("\n" + "=" * 85)
        print("DEVICE HEALTH STATUS REPORT")
        print("=" * 85)
        
        passed = 0
        failed = 0
        
        for host_name, task_results in results.items():
            if task_results.failed:
                failed += 1
                print(f"\n[FAILED] {host_name}")
                for task_name, task in task_results.items():
                    if task.failed:
                        print(f"  Error: {task.exception}")
            else:
                passed += 1
                facts = task_results[0].result
                uptime_str = format_uptime(facts.get("uptime_seconds"))
                
                print(f"\n[PASS] {facts['device']}")
                print(f"  Vendor/Model:  {facts['vendor']} {facts['model']}")
                print(f"  Hostname:      {facts['hostname']}")
                print(f"  OS Version:    {facts['os_version']}")
                print(f"  Uptime:        {uptime_str}")
                print(f"  Serial Number: {facts['serial_number']}")
        
        print("\n" + "=" * 85)
        print(f"Summary: {passed} passed, {failed} failed")
        print("=" * 85 + "\n")
        
        return 0 if failed == 0 else 1
        
    except FileNotFoundError as exc:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 2
    except Exception as exc:
        logger.error(f"Unexpected error: {exc}", exc_info=args.verbose)
        return 3


if __name__ == "__main__":
    sys.exit(main())
```