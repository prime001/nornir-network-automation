```python
#!/usr/bin/env python3
"""
Device Health Monitoring and Reporting

Monitors device health metrics (CPU, memory, temperature, uptime) across a network
using Nornir and NAPALM, generating a formatted health report with status indicators.

Usage:
    python device_health_monitor.py --filter "site:prod"
    python device_health_monitor.py --device router01 --warn-cpu 75 --warn-mem 80
    python device_health_monitor.py --format csv > health_report.csv

Prerequisites:
    - Nornir installed with NAPALM plugins
    - Inventory configured with device credentials (config.yaml)
    - Devices support NAPALM (Cisco IOS, Junos, Arista, etc.)
    - SSH/Telnet access configured for target devices
"""

import argparse
import logging
from typing import Dict
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.napalm_plugins import napalm_get


def setup_logging(verbosity: int = logging.INFO) -> None:
    """Configure logging output."""
    logging.basicConfig(
        level=verbosity,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def health_check(task: Task, warn_cpu: int, warn_mem: int) -> Result:
    """
    Gather device health metrics (CPU, memory, temperature, uptime).
    
    Args:
        task: Nornir task object
        warn_cpu: CPU usage warning threshold (percentage)
        warn_mem: Memory usage warning threshold (percentage)
    
    Returns:
        Nornir Result containing device health data
    """
    try:
        facts = task.run(napalm_get, getters=["facts"])
        environment = task.run(napalm_get, getters=["environment"])
        
        facts_data = facts.result["facts"]
        env_data = environment.result.get("environment", {})
        
        health_metrics = {
            "hostname": facts_data.get("hostname"),
            "vendor": facts_data.get("vendor"),
            "model": facts_data.get("model"),
            "uptime_seconds": facts_data.get("uptime_seconds", 0),
            "serial_number": facts_data.get("serial_number"),
            "os_version": facts_data.get("os_version"),
        }
        
        cpu_list = env_data.get("cpu", {})
        if cpu_list:
            avg_cpu = sum(
                cpu.get("%usage", 0) for cpu in cpu_list.values()
            ) / len(cpu_list)
            health_metrics["cpu_usage"] = round(avg_cpu, 2)
            health_metrics["cpu_status"] = "WARN" if avg_cpu > warn_cpu else "OK"
        else:
            health_metrics["cpu_usage"] = None
            health_metrics["cpu_status"] = "UNKNOWN"
        
        memory_list = env_data.get("memory", {})
        if memory_list:
            mem_usage = memory_list.get("ram", {}).get("%usage")
            health_metrics["memory_usage"] = mem_usage
            health_metrics["memory_status"] = "WARN" if mem_usage and mem_usage > warn_mem else "OK"
        else:
            health_metrics["memory_usage"] = None
            health_metrics["memory_status"] = "UNKNOWN"
        
        temp_list = env_data.get("temperature", {})
        max_temp = 0
        for temp_sensor in temp_list.values():
            current = temp_sensor.get("current_temperature", 0)
            max_temp = max(max_temp, current)
        health_metrics["max_temperature"] = round(max_temp, 2) if max_temp else None
        
        return Result(host=task.host, result=health_metrics)
    
    except Exception as e:
        return Result(
            host=task.host,
            result={},
            failed=True,
            exception=e
        )


def format_uptime(seconds: int) -> str:
    """Convert uptime seconds to human-readable format."""
    if not seconds:
        return "Unknown"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def print_health_report(results: Dict, format_type: str = "text") -> None:
    """
    Print device health report in specified format.
    
    Args:
        results: Dictionary of health check results
        format_type: Output format ('text' or 'csv')
    """
    if format_type == "csv":
        print("Device,Vendor,Model,Uptime,CPU%,Memory%,MaxTemp°C,CPU_Status,Mem_Status")
        for host, data in results.items():
            if data:
                print(
                    f"{data.get('hostname', host)},"
                    f"{data.get('vendor', 'N/A')},"
                    f"{data.get('model', 'N/A')},"
                    f"{format_uptime(data.get('uptime_seconds', 0))},"
                    f"{data.get('cpu_usage', 'N/A')},"
                    f"{data.get('memory_usage', 'N/A')},"
                    f"{data.get('max_temperature', 'N/A')},"
                    f"{data.get('cpu_status', 'UNKNOWN')},"
                    f"{data.get('memory_status', 'UNKNOWN')}"
                )
    else:
        print("\n" + "="*80)
        print("DEVICE HEALTH REPORT")
        print("="*80 + "\n")
        for host, data in results.items():
            if data:
                status_icon = "✓" if data.get('cpu_status') == 'OK' and data.get('memory_status') == 'OK' else "⚠"
                print(f"{status_icon} {data.get('hostname', host)}")
                print(f"  Vendor: {data.get('vendor')} | Model: {data.get('model')}")
                print(f"  Uptime: {format_uptime(data.get('uptime_seconds', 0))}")
                print(f"  CPU: {data.get('cpu_usage', 'N/A')}% [{data.get('cpu_status', 'UNKNOWN')}]")
                print(f"  Memory: {data.get('memory_usage', 'N/A')}% [{data.get('memory_status', 'UNKNOWN')}]")
                print(f"  Max Temperature: {data.get('max_temperature', 'N/A')}°C")
                print()


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor device health metrics across network infrastructure"
    )
    parser.add_argument(
        "--filter",
        type=str,
        help="Nornir filter string (e.g., 'site:prod')"
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Specific device hostname to check"
    )
    parser.add_argument(
        "--warn-cpu",
        type=int,
        default=80,
        help="CPU warning threshold percentage (default: 80)"
    )
    parser.add_argument(
        "--warn-mem",
        type=int,
        default=85,
        help="Memory warning threshold percentage (default: 85)"
    )
    parser.add_argument(
        "--format",
        choices=["text", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.device:
            nr = nr.filter(name=args.device)
        elif args.filter:
            nr = nr.filter(filter_func=lambda host: args.filter in str(host.vars))
        
        if not nr.inventory.hosts:
            logging.warning("No hosts match filter criteria")
            return
        
        results = nr.run(
            task=health_check,
            warn_cpu=args.warn_cpu,
            warn_mem=args.warn_mem,
            num_workers=5
        )
        
        health_data = {}
        for host, task_result in results.items():
            if task_result[0].result:
                health_data[host] = task_result[0].result
            else:
                logging.error(f"Failed to get health data for {host}")
        
        print_health_report(health_data, args.format)
    
    except Exception as e:
        logging.error(f"Health check failed: {e}")
        raise


if __name__ == "__main__":
    main()
```