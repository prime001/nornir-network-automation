```python
"""
Network Device Health Check and Diagnostics Tool

Purpose:
    Performs health checks on network devices including connectivity tests,
    uptime verification, and basic system diagnostics. Useful for identifying
    failed or degraded devices in the network.

Usage:
    python health_check.py --devices all --output report.json
    python health_check.py --devices router1,router2 --username admin --password pass
    python health_check.py --failed-only

Prerequisites:
    - Nornir installed with netmiko/napalm
    - nornir inventory configured (config.yaml)
    - Device SSH connectivity
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def health_check(task: Task) -> Result:
    """Perform health check on device including facts and interface status."""
    health_status = {
        "device": task.host.name,
        "timestamp": datetime.now().isoformat(),
        "reachable": False,
        "uptime": None,
        "facts": {},
        "interfaces_up": 0,
        "interfaces_down": 0
    }
    
    try:
        result = task.run(
            napalm_get,
            getters=["facts", "interfaces"],
            platform=task.host.platform
        )
        
        if result and result[0].result:
            health_status["reachable"] = True
            facts = result[0].result.get("facts", {})
            interfaces = result[0].result.get("interfaces", {})
            
            health_status["uptime"] = facts.get("uptime", 0)
            health_status["facts"] = {
                "model": facts.get("model", "Unknown"),
                "os_version": facts.get("os_version", "Unknown"),
                "serial_number": facts.get("serial_number", "Unknown"),
                "total_interfaces": len(interfaces)
            }
            
            for iface_name, iface_data in interfaces.items():
                if iface_data.get("is_up"):
                    health_status["interfaces_up"] += 1
                else:
                    health_status["interfaces_down"] += 1
        
        return Result(host=task.host, result=health_status)
    
    except Exception as e:
        logger.warning(f"Health check failed for {task.host.name}: {e}")
        health_status["error"] = str(e)
        return Result(host=task.host, result=health_status)


def format_uptime(seconds: int) -> str:
    """Convert uptime in seconds to human-readable format."""
    if not seconds:
        return "Unknown"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--devices',
        default='all',
        help='Target devices (comma-separated list or "all")'
    )
    parser.add_argument('--username', help='Device username')
    parser.add_argument('--password', help='Device password')
    parser.add_argument(
        '--output',
        type=Path,
        help='Output report file (JSON format)'
    )
    parser.add_argument(
        '--failed-only',
        action='store_true',
        help='Display only failed/unreachable devices'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Nornir configuration file'
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.config)
        
        if args.devices != 'all':
            device_list = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(name__in=device_list)
        
        logger.info(f"Starting health check on {len(nr.inventory.hosts)} devices")
        results = nr.run(task=health_check)
        
        health_report = []
        failed_devices = []
        
        for host_name, task_results in results.items():
            status = task_results[0].result
            health_report.append(status)
            
            if not status["reachable"]:
                failed_devices.append(host_name)
        
        print("\n" + "=" * 70)
        print("NETWORK DEVICE HEALTH CHECK REPORT")
        print("=" * 70)
        print(f"Total Devices Checked: {len(health_report)}")
        print(f"Reachable: {sum(1 for h in health_report if h['reachable'])}")
        print(f"Unreachable: {len(failed_devices)}")
        
        if not args.failed_only:
            print("\nDevice Status:")
            for status in sorted(
                health_report,
                key=lambda x: (not x["reachable"], x["device"])
            ):
                if status["reachable"]:
                    uptime = format_uptime(status.get("uptime", 0))
                    print(
                        f"  ✓ {status['device']:<20} | "
                        f"Uptime: {uptime:<15} | "
                        f"OS: {status['facts'].get('os_version', 'N/A'):<12} | "
                        f"Interfaces: {status['interfaces_up']}/{status['facts'].get('total_interfaces', 0)}"
                    )
                else:
                    print(f"  ✗ {status['device']:<20} | UNREACHABLE")
                    if "error" in status:
                        print(f"      Error: {status['error']}")
        
        if failed_devices:
            print(f"\nAlert: Failed Devices: {', '.join(failed_devices)}")
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(health_report, f, indent=2)
            logger.info(f"Health report saved to {args.output}")
        
        sys.exit(0 if not failed_devices else 1)
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```