```python
"""
Device Health Check - Gathers CPU, memory, and uptime metrics.

Purpose:
    Collects device health metrics (CPU, memory, uptime) from network devices
    using NAPALM getters and displays a summary report.

Usage:
    python 009_device_health_check.py --device all --username admin --password secret

Prerequisites:
    - nornir >= 3.0
    - napalm
    - nornir inventory (hosts.yaml, groups.yaml)
    - Device credentials in environment or CLI args
"""

import logging
import argparse
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.napalm import napalm_get
from nornir.plugins.functions.text import print_result

logger = logging.getLogger(__name__)


def health_check(task: Task) -> Result:
    """Gather CPU, memory, and uptime metrics from device."""
    try:
        napalm_result = task.run(
            napalm_get,
            getters=["get_facts", "get_environment"]
        )
        
        data = napalm_result.result
        facts = data.get("get_facts", {})
        env = data.get("get_environment", {})
        
        health_info = {
            "hostname": facts.get("hostname", "N/A"),
            "os_version": facts.get("os_version", "N/A"),
            "uptime_seconds": facts.get("uptime_seconds", 0),
            "model": facts.get("model", "N/A"),
        }
        
        cpu_list = env.get("cpu", {}).get("CPU", [])
        if cpu_list:
            health_info["cpu_usage"] = cpu_list[0].get("%usage", "N/A")
        
        memory = env.get("memory", {})
        if memory:
            health_info["memory_usage"] = memory.get("used_percent", "N/A")
        
        return Result(host=task.host, result=health_info)
    
    except Exception as e:
        logger.error(f"Error on {task.host}: {e}")
        return Result(
            host=task.host,
            result={"error": str(e)},
            failed=True
        )


def main():
    """Load inventory and execute health checks."""
    parser = argparse.ArgumentParser(
        description="Collect device health metrics"
    )
    parser.add_argument(
        "--device",
        default="all",
        help="Target device or 'all' (default: all)"
    )
    parser.add_argument("--username", help="Device username")
    parser.add_argument("--password", help="Device password")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.device != "all":
            nr = nr.filter(name=args.device)
        
        if args.username:
            for host in nr.inventory.hosts.values():
                host.username = args.username
        
        if args.password:
            for host in nr.inventory.hosts.values():
                host.password = args.password
        
        host_count = len(nr.inventory.hosts)
        logger.info(f"Starting health check on {host_count} device(s)")
        
        results = nr.run(task=health_check)
        print_result(results)
        
        passed = sum(1 for r in results.values() if r[0].ok)
        failed = sum(1 for r in results.values() if not r[0].ok)
        logger.info(f"Health check complete: {passed} passed, {failed} failed")
        
        return 0 if failed == 0 else 1
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```