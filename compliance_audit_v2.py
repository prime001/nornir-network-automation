```python
"""
Device Health Checker — Nornir-based network device health monitoring.

Purpose:
    Monitor critical health metrics across network devices including CPU utilization,
    memory usage, interface errors, and system uptime. Generates a consolidated health
    report with severity flags for degraded conditions.

Usage:
    python device_health_checker.py --devices router1,router2
    python device_health_checker.py --all --threshold 80
    python device_health_checker.py --devices core-switches --format json

Prerequisites:
    - Nornir inventory configured with network devices
    - SSH connectivity to devices via Netmiko
    - Credentials provided in inventory or environment
    - Device OS: Cisco IOS, IOS-XE, NXOS, or similar

Output:
    Health status table with CPU/memory metrics and interface error counts.
    Warnings flagged for CPU >80%, memory >85%, or interface errors >10.
"""

import argparse
import json
import logging
import sys
from typing import Dict, List, Any
from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_health_metrics(device_name: str, show_version: str, 
                        show_interfaces: str) -> Dict[str, Any]:
    """Extract CPU, memory, uptime, and interface error metrics from outputs."""
    metrics = {
        "device": device_name,
        "cpu_percent": None,
        "memory_percent": None,
        "uptime": None,
        "interface_errors": 0,
        "interfaces_down": 0,
        "status": "HEALTHY"
    }
    
    try:
        for line in show_version.split('\n'):
            if 'CPU' in line and '%' in line:
                try:
                    cpu_val = int(line.split()[-1].rstrip('%'))
                    metrics["cpu_percent"] = cpu_val
                except (ValueError, IndexError):
                    pass
            if 'Memory' in line and '%' in line:
                try:
                    mem_val = int(line.split()[-1].rstrip('%'))
                    metrics["memory_percent"] = mem_val
                except (ValueError, IndexError):
                    pass
            if 'uptime' in line.lower():
                metrics["uptime"] = line.strip()
        
        error_count = 0
        down_count = 0
        for line in show_interfaces.split('\n'):
            if 'errors' in line.lower() and line.split():
                try:
                    error_val = int(line.split()[-1])
                    if error_val > 0:
                        error_count += error_val
                except (ValueError, IndexError):
                    pass
            if ' down ' in line.lower():
                down_count += 1
        
        metrics["interface_errors"] = error_count
        metrics["interfaces_down"] = down_count
        
        if metrics["cpu_percent"] and metrics["cpu_percent"] > 85:
            metrics["status"] = "CRITICAL"
        elif (metrics["cpu_percent"] and metrics["cpu_percent"] > 75) or \
             (metrics["memory_percent"] and metrics["memory_percent"] > 85) or \
             error_count > 10:
            metrics["status"] = "WARNING"
    
    except Exception as e:
        logger.warning(f"Error parsing metrics for {device_name}: {e}")
        metrics["status"] = "ERROR"
    
    return metrics


def check_device_health(task, threshold: int = 80) -> Dict[str, Any]:
    """Nornir task to collect health metrics from a device."""
    device = task.host.name
    logger.info(f"Checking health for {device}")
    
    try:
        r_version = task.run(
            netmiko_send_command,
            command_string="show version"
        )
        r_interfaces = task.run(
            netmiko_send_command,
            command_string="show interfaces"
        )
        
        metrics = parse_health_metrics(
            device,
            r_version.result,
            r_interfaces.result
        )
        
        return metrics
    
    except Exception as e:
        logger.error(f"Failed to check {device}: {e}")
        return {
            "device": device,
            "status": "UNREACHABLE",
            "error": str(e)
        }


def display_results(results: Dict[str, Dict], output_format: str = "text") -> None:
    """Display health check results."""
    if output_format == "json":
        print(json.dumps(results, indent=2))
        return
    
    print("\n" + "=" * 90)
    print(f"{'Device':<20} {'CPU':<12} {'Memory':<12} {'Int Errors':<15} {'Status':<15}")
    print("=" * 90)
    
    for device, metrics in results.items():
        cpu_str = f"{metrics['cpu_percent']}%" if metrics['cpu_percent'] is not None else "N/A"
        mem_str = f"{metrics['memory_percent']}%" if metrics['memory_percent'] is not None else "N/A"
        status = metrics['status']
        errors = metrics.get('interface_errors', 0)
        
        print(f"{device:<20} {cpu_str:<12} {mem_str:<12} {errors:<15} {status:<15}")
    
    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Check health metrics on network devices"
    )
    parser.add_argument(
        "--devices",
        type=str,
        help="Comma-separated device names (default: all)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all devices in inventory"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=80,
        help="CPU threshold for warning (default: 80)"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Nornir config file"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level"
    )
    
    args = parser.parse_args()
    logger.setLevel(getattr(logging, args.log_level))
    
    try:
        nr = InitNornir(config_file=args.config)
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
        
        logger.info(f"Running health checks on {len(nr.inventory.hosts)} devices")
        
        results = {}
        for host in nr.inventory.hosts.values():
            metrics = check_device_health(None, args.threshold)
            if metrics:
                results[host.name] = metrics
        
        display_results(results, args.format)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```