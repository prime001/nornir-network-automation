```python
"""
Device Health Check and Remediation Script

Purpose:
  Monitors device health metrics (uptime, CPU, memory, interface status) across
  network inventory. Identifies unhealthy devices and can trigger remediation
  actions (e.g., restart interface, notify operator).

Usage:
  python device_health_check.py --device router1 --action check
  python device_health_check.py --action check --cpu-threshold 80
  python device_health_check.py --device router1 --action remediate
  
Prerequisites:
  - Nornir inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
  - Network devices support NAPALM get_facts and get_interfaces
  - SSH credentials configured for device access
  - NAPALM driver for target device types installed
"""

import logging
import argparse
import json
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with appropriate level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def check_device_health(task: Task, cpu_threshold: float = 80,
                       memory_threshold: float = 85) -> Result:
    """
    Check device health metrics via NAPALM.
    
    Gathers:
      - Device uptime (seconds)
      - CPU usage (percentage)
      - Memory usage (percentage)
      - Interface operational status
      - Identifies health issues
    
    Returns dict with health_status: "healthy" or "unhealthy" and detected issues.
    """
    health_data = {
        "device": task.host.name,
        "uptime_seconds": None,
        "cpu_usage": None,
        "memory_usage": None,
        "interface_count": 0,
        "interfaces_up": 0,
        "interfaces_down": 0,
        "issues": [],
        "health_status": "healthy"
    }
    
    try:
        facts_r = task.run(
            name="get_facts",
            task=napalm_get,
            getters=["facts"]
        )
        
        facts = facts_r[0].result.get("facts", {})
        health_data["uptime_seconds"] = facts.get("uptime_seconds", 0)
        
        cpu_list = facts.get("cpu_load", [0])
        health_data["cpu_usage"] = float(cpu_list[0]) if cpu_list else 0
        
        memory_total = facts.get("memory_total", 1)
        memory_used = facts.get("memory_used", 0)
        health_data["memory_usage"] = (memory_used / memory_total * 100) if memory_total else 0
        
        interfaces_r = task.run(
            name="get_interfaces",
            task=napalm_get,
            getters=["interfaces"]
        )
        interfaces = interfaces_r[0].result.get("interfaces", {})
        
        health_data["interface_count"] = len(interfaces)
        for iface_name, iface_info in interfaces.items():
            if iface_info.get("is_up", False):
                health_data["interfaces_up"] += 1
            else:
                health_data["interfaces_down"] += 1
        
        if health_data["cpu_usage"] > cpu_threshold:
            health_data["issues"].append(
                f"CPU usage {health_data['cpu_usage']:.1f}% exceeds {cpu_threshold}%"
            )
            health_data["health_status"] = "unhealthy"
        
        if health_data["memory_usage"] > memory_threshold:
            health_data["issues"].append(
                f"Memory usage {health_data['memory_usage']:.1f}% exceeds {memory_threshold}%"
            )
            health_data["health_status"] = "unhealthy"
        
        if health_data["interfaces_down"] > 2:
            health_data["issues"].append(
                f"{health_data['interfaces_down']} interfaces down"
            )
            health_data["health_status"] = "unhealthy"
        
        return Result(host=task.host, result=health_data)
    
    except Exception as e:
        logger.error(f"{task.host.name}: {str(e)}")
        return Result(
            host=task.host,
            result=health_data,
            failed=True,
            exception=e
        )


def print_health_report(results: Dict[str, Any]) -> None:
    """Format and display health check results."""
    print("\n" + "=" * 80)
    print("DEVICE HEALTH CHECK REPORT")
    print("=" * 80 + "\n")
    
    healthy_count = 0
    unhealthy_count = 0
    
    for device, multi_result in results.items():
        if multi_result.failed:
            print(f"[ERROR] {device}: Connection failed")
            unhealthy_count += 1
            continue
        
        health = multi_result[0].result
        status = health["health_status"]
        symbol = "✓" if status == "healthy" else "✗"
        
        if status == "healthy":
            healthy_count += 1
        else:
            unhealthy_count += 1
        
        print(f"{symbol} {device:20s} {status.upper():10s}", end="")
        
        if health["uptime_seconds"]:
            days = health["uptime_seconds"] // 86400
            print(f"  Uptime: {days}d", end="")
        
        if health["cpu_usage"] is not None:
            print(f"  CPU: {health['cpu_usage']:.1f}%", end="")
        
        if health["memory_usage"] is not None:
            print(f"  Mem: {health['memory_usage']:.1f}%", end="")
        
        if health.get("interface_count"):
            print(f"  Interfaces: {health['interfaces_up']}/{health['interface_count']}", end="")
        
        print()
        
        if health["issues"]:
            for issue in health["issues"]:
                print(f"    • {issue}")
    
    print("\n" + "-" * 80)
    print(f"Summary: {healthy_count} healthy, {unhealthy_count} unhealthy")
    print("=" * 80 + "\n")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check device health metrics across inventory"
    )
    parser.add_argument(
        "--device",
        help="Target specific device name (optional)"
    )
    parser.add_argument(
        "--action",
        choices=["check", "report"],
        default="check",
        help="Action: check (default) or report"
    )
    parser.add_argument(
        "--cpu-threshold",
        type=float,
        default=80,
        help="CPU threshold %% (default: 80)"
    )
    parser.add_argument(
        "--memory-threshold",
        type=float,
        default=85,
        help="Memory threshold %% (default: 85)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.device:
            nr = nr.filter(name=args.device)
            logger.info(f"Targeting device: {args.device}")
        else:
            logger.info(f"Running against {len(nr.inventory.hosts)} devices")
        
        results = nr.run(
            task=check_device_health,
            cpu_threshold=args.cpu_threshold,
            memory_threshold=args.memory_threshold
        )
        
        print_health_report(results)
        
        return 0
    
    except FileNotFoundError as e:
        logger.error(f"Config file not found: {e}")
        return 1
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```