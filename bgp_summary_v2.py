```python
"""
Device Health Check Script

Gathers system health metrics from network devices including uptime, memory usage,
CPU utilization, and version information. Useful for capacity planning and device
monitoring across your network infrastructure.

Usage:
    python device_health_check.py -i inventory.yaml
    python device_health_check.py -i inventory.yaml --device core-01
    python device_health_check.py -i inventory.yaml --format json

Prerequisites:
    - Nornir and netmiko installed
    - Valid network device credentials in inventory
    - Device SSH/telnet access configured
"""

import argparse
import json
import logging
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_utils.plugins.functions import print_result
from netmiko import ConnectHandler
from netmiko.ssh_exception import NetmikoTimeoutException


logger = logging.getLogger(__name__)


def get_device_health(task: Task) -> Result:
    """Gather device health metrics via netmiko."""
    try:
        device = task.host
        conn_params = {
            "device_type": device.platform,
            "host": device.hostname,
            "username": device.username,
            "password": device.password,
            "timeout": 10,
            "global_delay_factor": 2,
        }
        
        with ConnectHandler(**conn_params) as net_connect:
            metrics = {"hostname": device.hostname, "platform": device.platform}
            
            if device.platform == "cisco_ios":
                version_out = net_connect.send_command("show version")
                uptime_line = [l for l in version_out.split("\n") if "uptime" in l.lower()]
                metrics["uptime"] = uptime_line[0].strip() if uptime_line else "Unknown"
                
                memory_out = net_connect.send_command("show processes memory | include Processor")
                metrics["memory"] = memory_out.strip()
                
            elif device.platform == "arista_eos":
                uptime_out = net_connect.send_command("show uptime")
                metrics["uptime"] = uptime_out.split("\n")[0].strip()
                
                version_out = net_connect.send_command("show version | json")
                try:
                    version_data = json.loads(version_out)
                    metrics["model"] = version_data.get("modelName", "Unknown")
                    metrics["version"] = version_data.get("version", "Unknown")
                except json.JSONDecodeError:
                    metrics["version"] = "Unable to parse"
                    
            elif device.platform in ["juniper_junos", "juniper"]:
                uptime_out = net_connect.send_command("show system uptime")
                metrics["uptime"] = uptime_out.split("\n")[0].strip()
                
                version_out = net_connect.send_command("show version")
                for line in version_out.split("\n"):
                    if "Junos:" in line:
                        metrics["version"] = line.strip()
                        break
            else:
                metrics["status"] = "Unsupported platform"
            
            return Result(host=task.host, result=metrics)
            
    except NetmikoTimeoutException:
        logger.error(f"Timeout connecting to {task.host.hostname}")
        return Result(
            host=task.host,
            result={"hostname": task.host.hostname, "error": "Connection timeout"},
            failed=True,
        )
    except Exception as e:
        logger.error(f"Error gathering health for {task.host.hostname}: {str(e)}")
        return Result(
            host=task.host,
            result={"hostname": task.host.hostname, "error": str(e)},
            failed=True,
        )


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Gather device health metrics from network devices"
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory.yaml",
        help="Path to nornir inventory file",
    )
    parser.add_argument(
        "-d", "--device",
        help="Filter to specific device by hostname",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(name=args.device)
            if not nr.inventory.hosts:
                logger.error(f"Device '{args.device}' not found in inventory")
                return 1
        
        logger.info(f"Gathering health metrics from {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=get_device_health, num_workers=4)
        
        if args.format == "json":
            output = {}
            for host, task_result in results.items():
                output[host] = task_result[0].result
            print(json.dumps(output, indent=2))
        else:
            print_result(results)
        
        return 0
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
```