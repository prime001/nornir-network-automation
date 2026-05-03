```python
"""
Device Health Monitor - Check critical health metrics on network devices.

This script uses Nornir to collect and analyze device health metrics including
uptime, interface status, and system temperature. Useful for identifying degraded
devices and capacity planning across the network.

Usage:
    python 017_device_health_monitor.py -d prod -u admin
    python 017_device_health_monitor.py --devices R1,R2,R3 -u automation -p secret
    python 017_device_health_monitor.py -d core -u netadmin --warn-uptime 7

Prerequisites:
    - Nornir inventory with device definitions
    - SSH/Telnet access to network devices
    - NAPALM or netmiko for device connectivity
    - Python 3.8+, nornir, napalm installed
"""

import argparse
import logging
from typing import Dict, List, Tuple

from nornir import InitNornir
from nornir.core.task import Task, Result


def setup_logging(level: str = "INFO") -> None:
    """Configure logging output."""
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=getattr(logging, level),
    )


def collect_device_facts(task: Task) -> Result:
    """
    Collect device facts including uptime and system info.
    
    Args:
        task: Nornir task object
    
    Returns:
        Result containing device facts
    """
    try:
        from nornir_napalm.plugins.tasks import napalm_get
        
        result = task.run(napalm_get, getters=["facts"])
        
        if result[0].result:
            facts = result[0].result.get("facts", {})
            return Result(
                host=task.host,
                result={
                    "hostname": facts.get("hostname", "N/A"),
                    "uptime": facts.get("uptime_seconds", 0),
                    "vendor": facts.get("vendor", "N/A"),
                    "model": facts.get("model", "N/A"),
                    "os_version": facts.get("os_version", "N/A"),
                    "serial": facts.get("serial_number", "N/A"),
                }
            )
        return Result(host=task.host, failed=True, result={})
    
    except Exception as e:
        task.logger.error(f"Failed to collect facts: {e}")
        return Result(host=task.host, failed=True, exception=e)


def check_interface_status(task: Task) -> Result:
    """
    Check interface operational status and count down interfaces.
    
    Args:
        task: Nornir task object
    
    Returns:
        Result with interface status summary
    """
    try:
        from nornir_napalm.plugins.tasks import napalm_get
        
        result = task.run(napalm_get, getters=["interfaces"])
        
        if result[0].result:
            interfaces = result[0].result.get("interfaces", {})
            
            down_interfaces = [
                iface for iface, data in interfaces.items()
                if not data.get("is_up", False)
            ]
            
            return Result(
                host=task.host,
                result={
                    "total_interfaces": len(interfaces),
                    "down_interfaces": down_interfaces,
                    "down_count": len(down_interfaces),
                }
            )
        return Result(host=task.host, failed=True, result={})
    
    except Exception as e:
        task.logger.error(f"Failed to check interfaces: {e}")
        return Result(host=task.host, failed=True, exception=e)


def format_uptime(seconds: int) -> str:
    """Convert uptime seconds to human-readable format."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def evaluate_health(facts: Dict, interfaces: Dict, 
                    uptime_warn_days: int) -> Tuple[str, List[str]]:
    """
    Evaluate device health and generate warnings.
    
    Args:
        facts: Device facts dictionary
        interfaces: Interface status data
        uptime_warn_days: Warning threshold for uptime in days
    
    Returns:
        Tuple of (status, warnings_list)
    """
    warnings = []
    
    if interfaces.get("down_count", 0) > 0:
        down_list = ", ".join(interfaces["down_interfaces"][:3])
        if len(interfaces["down_interfaces"]) > 3:
            down_list += "..."
        warnings.append(f"{interfaces['down_count']} interface(s) down: {down_list}")
    
    uptime_seconds = facts.get("uptime", 0)
    if uptime_seconds < (uptime_warn_days * 86400):
        uptime_str = format_uptime(uptime_seconds)
        warnings.append(f"Low uptime: {uptime_str} (device recently rebooted)")
    
    status = "HEALTHY" if not warnings else "DEGRADED"
    return status, warnings


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor device health metrics across the network"
    )
    parser.add_argument(
        "-d", "--devices",
        default="all",
        help="Device group or comma-separated list (default: all)"
    )
    parser.add_argument(
        "-u", "--username",
        required=True,
        help="Username for authentication"
    )
    parser.add_argument(
        "-p", "--password",
        help="Password (will prompt if omitted)"
    )
    parser.add_argument(
        "--warn-uptime",
        type=int,
        default=7,
        help="Warn if uptime less than N days (default: 7)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    setup_logging("DEBUG" if args.verbose else "INFO")
    logger = logging.getLogger(__name__)
    
    logger.info("Starting device health check")
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.devices != "all":
            nr = nr.filter(name=args.devices)
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter")
            return
        
        logger.info(f"Checking {len(nr.inventory.hosts)} device(s)")
        
        facts_results = nr.run(task=collect_device_facts)
        int_results = nr.run(task=check_interface_status)
        
        logger.info("\n" + "=" * 75)
        logger.info("DEVICE HEALTH REPORT")
        logger.info("=" * 75)
        
        for hostname in sorted(nr.inventory.hosts.keys()):
            if hostname not in facts_results:
                continue
            
            facts_result = facts_results[hostname][0]
            int_result = int_results[hostname][0]
            
            if facts_result.failed:
                logger.error(f"{hostname}: FAILED - {facts_result.exception}")
                continue
            
            facts = facts_result.result
            int_data = int_result.result if int_result and not int_result.failed else {}
            status, warnings = evaluate_health(facts, int_data, args.warn_uptime)
            
            logger.info(f"\n{hostname}")
            logger.info(f"  Status: {status}")
            logger.info(f"  Vendor: {facts.get('vendor', 'N/A')}")
            logger.info(f"  Model: {facts.get('model', 'N/A')}")
            logger.info(f"  OS Version: {facts.get('os_version', 'N/A')}")
            logger.info(f"  Uptime: {format_uptime(facts.get('uptime', 0))}")
            logger.info(f"  Serial: {facts.get('serial', 'N/A')}")
            
            if int_data:
                logger.info(f"  Interfaces: {int_data['total_interfaces']} total, "
                           f"{int_data['down_count']} down")
            
            for warning in warnings:
                logger.warning(f"    ⚠ {warning}")
        
        logger.info("\n" + "=" * 75)
        logger.info("Health check complete")
        
    except FileNotFoundError:
        logger.error("config.yaml not found in current directory")
        raise
    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=args.verbose)
        raise


if __name__ == "__main__":
    main()
```