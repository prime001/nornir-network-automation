```python
"""
Device Health Status Report - Nornir Network Automation

Purpose:
    Collects system health metrics from network devices including uptime,
    CPU/memory utilization, interface status, and connectivity diagnostics.
    Useful for quick network health checks and identifying offline/degraded
    devices.

Usage:
    python device_health.py -i inventory.yaml -c credentials.yaml
    python device_health.py -i inventory.yaml -c credentials.yaml --group routers
    python device_health.py -i inventory.yaml -c credentials.yaml --format json

Prerequisites:
    - Nornir with netmiko driver
    - Network devices with SSH access
    - Supported platforms: Cisco IOS/XE/XR, Arista, Juniper
    - Credentials configured in secrets backend
"""

import argparse
import json
import logging
from typing import Dict, Any
from datetime import datetime

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logger = logging.getLogger(__name__)


def parse_uptime(uptime_str: str) -> str:
    """Extract uptime from version output."""
    try:
        for line in uptime_str.split('\n'):
            if 'uptime' in line.lower():
                return line.strip()
    except Exception:
        pass
    return "Unknown"


def collect_health_metrics(task: Task) -> Result:
    """Collect device health metrics via netmiko."""
    health = {
        'hostname': task.host.name,
        'timestamp': datetime.now().isoformat(),
        'reachable': False,
        'metrics': {}
    }
    
    try:
        # Get version and uptime
        version_output = task.run(
            netmiko_send_command,
            command_string="show version",
            name="version"
        )
        
        if version_output.result:
            health['reachable'] = True
            health['metrics']['uptime'] = parse_uptime(version_output.result)
        
        # Get interface status
        int_brief = task.run(
            netmiko_send_command,
            command_string="show interface brief",
            name="interfaces"
        )
        
        if int_brief.result:
            lines = int_brief.result.split('\n')
            up_count = sum(1 for line in lines if 'up' in line.lower() and 'down' not in line.lower())
            down_count = sum(1 for line in lines if 'down' in line.lower())
            health['metrics']['interfaces_up'] = up_count
            health['metrics']['interfaces_down'] = down_count
        
        # Get system resources (CPU, memory)
        resource_cmd = {
            'ios': 'show processes cpu sorted',
            'iosxe': 'show processes cpu sorted',
            'iosxr': 'show processes cpu',
            'eos': 'show system resources',
            'junos': 'show system processes extensive'
        }.get(task.host.platform, 'show processes cpu')
        
        try:
            resources = task.run(
                netmiko_send_command,
                command_string=resource_cmd,
                name="resources"
            )
            if resources.result:
                # Extract first line of output for CPU
                cpu_line = resources.result.split('\n')[0]
                health['metrics']['cpu_line'] = cpu_line[:80]
        except Exception as e:
            logger.debug(f"Could not get resource info: {e}")
        
        return Result(
            host=task.host,
            result=health,
            name="health_check"
        )
    
    except Exception as e:
        logger.error(f"Failed to collect metrics from {task.host.name}: {e}")
        health['error'] = str(e)
        return Result(
            host=task.host,
            result=health,
            failed=True
        )


def format_output(results: Dict[str, Any], format_type: str) -> None:
    """Format and display results."""
    if format_type == "json":
        output = {}
        for host, task_results in results.items():
            if task_results:
                output[host] = task_results[0].result
            else:
                output[host] = {"error": "No result"}
        print(json.dumps(output, indent=2))
    
    else:
        print("\n" + "="*70)
        print("DEVICE HEALTH STATUS REPORT")
        print("="*70 + "\n")
        
        for host, task_results in sorted(results.items()):
            if not task_results:
                print(f"{host}: No result")
                continue
            
            data = task_results[0].result
            status = "REACHABLE" if data.get('reachable') else "UNREACHABLE"
            status_sym = "✓" if data.get('reachable') else "✗"
            
            print(f"{status_sym} {host:25} [{status}]")
            
            if 'error' in data:
                print(f"   Error: {data['error']}\n")
                continue
            
            metrics = data.get('metrics', {})
            if metrics.get('uptime'):
                print(f"   Uptime: {metrics['uptime']}")
            if 'interfaces_up' in metrics:
                print(f"   Interfaces: {metrics['interfaces_up']} up, "
                      f"{metrics['interfaces_down']} down")
            if metrics.get('cpu_line'):
                print(f"   CPU: {metrics['cpu_line']}")
            print()


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Collect device health metrics across network"
    )
    parser.add_argument("-i", "--inventory", required=True,
                        help="Inventory file path")
    parser.add_argument("-c", "--credentials", required=True,
                        help="Credentials file path")
    parser.add_argument("-g", "--group", help="Filter by group")
    parser.add_argument("-f", "--format", choices=["text", "json"],
                        default="text", help="Output format")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    try:
        nr = InitNornir(config_file="nornir_config.yaml")
        
        if args.group:
            nr = nr.filter(group=args.group)
        
        logger.info(f"Running health check on {len(nr.inventory.hosts)} devices")
        results = nr.run(task=collect_health_metrics, num_workers=10)
        
        format_output(dict(results), args.format)
        
        if results.failed_hosts:
            logger.warning(
                f"Health check failed for: {', '.join(results.failed_hosts.keys())}"
            )
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```