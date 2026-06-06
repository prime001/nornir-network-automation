```python
"""
Device System Health and Diagnostics Collector.

Gathers comprehensive health metrics from network devices including uptime,
resource utilization, interface errors, and reachability status. Useful for
operational monitoring and capacity planning.

Usage:
    python device_health_collector.py -i inventory.yaml -u admin -p password
    python device_health_collector.py -i inventory.yaml -u admin -p password --group core
    python device_health_collector.py -i inventory.yaml -u admin -p password --format json

Prerequisites:
    - Nornir installed (pip install nornir)
    - Netmiko or NAPALM installed for device connectivity
    - Network device credentials (SSH keys or username/password)
    - Supported platforms: Cisco IOS/XE/XR, Juniper Junos, Arista EOS

Author: Network Automation Portfolio
"""

import json
import logging
import argparse
from datetime import datetime
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the script."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def get_device_info(task: Task) -> Result:
    """Gather device version and uptime information."""
    device = task.host
    info = {"device": device.name, "platform": device.platform}
    
    try:
        cmd = "show version"
        result = task.run(
            netmiko_send_command,
            command_string=cmd,
            name="version_check"
        )
        
        if result[0].failed:
            return Result(host=device, failed=True, result="Failed to gather version info")
        
        info["version_output"] = result[0].result[:500]
        info["reachable"] = True
        
    except Exception as e:
        logger.warning(f"{device.name}: Unreachable - {e}")
        info["reachable"] = False
        info["error"] = str(e)
    
    return Result(host=device, result=info)


def get_interface_errors(task: Task) -> Result:
    """Gather interface error and discard counters."""
    device = task.host
    
    try:
        if "juniper" in device.platform.lower():
            cmd = "show interfaces brief"
        else:
            cmd = "show interfaces"
        
        result = task.run(
            netmiko_send_command,
            command_string=cmd,
            name="interface_check"
        )
        
        if result[0].failed:
            return Result(host=device, failed=True, result="Interface check failed")
        
        return Result(host=device, result={"interface_status": result[0].result[:300]})
        
    except Exception as e:
        logger.warning(f"{device.name}: Interface check failed - {e}")
        return Result(host=device, failed=True, result=str(e))


def health_check(task: Task) -> Result:
    """Comprehensive device health check workflow."""
    device = task.host
    health = {
        "device": device.name,
        "timestamp": datetime.now().isoformat(),
        "platform": device.platform,
        "checks": {}
    }
    
    # Gather device info
    info_result = task.run(get_device_info, name="device_info")
    health["checks"]["device_info"] = {
        "status": "pass" if not info_result[0].failed else "fail",
        "result": info_result[0].result
    }
    
    # Gather interface metrics
    iface_result = task.run(get_interface_errors, name="interface_errors")
    health["checks"]["interface_errors"] = {
        "status": "pass" if not iface_result[0].failed else "fail",
        "result": iface_result[0].result
    }
    
    # Determine overall health
    passed = sum(
        1 for c in health["checks"].values() if c["status"] == "pass"
    )
    health["overall_status"] = "healthy" if passed == len(health["checks"]) else "degraded"
    
    return Result(host=device, result=health)


def format_output(results: Dict[str, Any], output_format: str) -> str:
    """Format results for display."""
    if output_format == "json":
        return json.dumps(results, indent=2)
    
    lines = ["\n" + "="*70, "DEVICE HEALTH CHECK REPORT", "="*70 + "\n"]
    
    for device_name, device_data in results.items():
        if isinstance(device_data, dict) and "overall_status" in device_data:
            lines.extend([
                f"Device: {device_data['device']}",
                f"Status: {device_data['overall_status'].upper()}",
                f"Platform: {device_data['platform']}",
                f"Timestamp: {device_data['timestamp']}",
                "-" * 70,
                ""
            ])
        else:
            lines.append(f"{device_name}: {device_data}")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Comprehensive device health monitoring and diagnostics"
    )
    parser.add_argument(
        "-i", "--inventory",
        required=True,
        help="Path to nornir inventory file"
    )
    parser.add_argument(
        "-u", "--username",
        help="Device username"
    )
    parser.add_argument(
        "-p", "--password",
        help="Device password"
    )
    parser.add_argument(
        "-g", "--group",
        help="Target specific device group"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    try:
        logger.info("Initializing Nornir inventory")
        nr = InitNornir(config_file=args.inventory)
        
        if args.group:
            nr = nr.filter(group=args.group)
            logger.info(f"Filtered to group: {args.group}")
        
        logger.info(f"Running health checks on {len(nr.inventory.hosts)} device(s)")
        
        results = nr.run(
            task=health_check,
            num_workers=4,
            name="health_check"
        )
        
        report = {}
        for device_name, task_result in results.items():
            report[device_name] = task_result[0].result
        
        output = format_output(report, args.format)
        print(output)
        
        healthy_count = sum(
            1 for v in report.values() 
            if isinstance(v, dict) and v.get("overall_status") == "healthy"
        )
        logger.info(f"Health check complete: {healthy_count}/{len(report)} devices healthy")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```