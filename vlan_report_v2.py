#!/usr/bin/env python3
"""
Device Health Metrics Aggregator

Gathers and reports CPU, memory, and uptime metrics across a network fleet.
Uses NAPALM facts and environment getters to retrieve device health data,
aggregates results, and highlights devices exceeding resource thresholds.

Usage:
    python device_health.py -i inventory.yaml -g all
    python device_health.py -i inventory.yaml --group datacenter --cpu-warn 75

Prerequisites:
    - nornir with NAPALM plugin
    - Inventory YAML file with device groups and connection details
    - Devices supporting NAPALM 'facts' and 'environment' getters
    - tabulate library for formatted output
"""

import argparse
import logging
import sys
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get
from tabulate import tabulate


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def gather_health_metrics(task: Task) -> Result:
    """Retrieve health metrics from device using NAPALM getters."""
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        environment_result = task.run(napalm_get, getters=["environment"])
        
        facts = facts_result.result.get("facts", {})
        environment = environment_result.result.get("environment", {})
        
        uptime_seconds = facts.get("uptime", 0)
        uptime_days = uptime_seconds // 86400
        
        cpu_percent = 0.0
        cpu_info = environment.get("cpu", {})
        if isinstance(cpu_info, dict) and "" in cpu_info:
            cpu_list = cpu_info[""]
            if cpu_list:
                cpu_percent = float(cpu_list[0].get("%usage", 0))
        
        memory_info = environment.get("memory", {})
        mem_used = memory_info.get("used_ram", 0)
        mem_avail = memory_info.get("available_ram", 1)
        memory_percent = (mem_used / mem_avail * 100) if mem_avail > 0 else 0
        
        return Result(
            host=task.host,
            result={
                "uptime_days": uptime_days,
                "cpu_percent": round(cpu_percent, 1),
                "memory_percent": round(memory_percent, 1),
                "model": facts.get("model", "Unknown"),
            },
        )
    except Exception as e:
        logger.warning(f"{task.host.name}: {str(e)}")
        return Result(host=task.host, result=None, failed=True)


def print_report(results: dict, cpu_warn: int, mem_warn: int) -> int:
    """Print formatted health report. Returns exit code."""
    table = []
    alert_count = 0
    
    for hostname in sorted(results.keys()):
        task_results = results[hostname]
        
        if task_results[0].failed:
            table.append([hostname, "ERROR", "—", "—", "—"])
            alert_count += 1
            continue
        
        data = task_results[0].result
        cpu = data["cpu_percent"]
        mem = data["memory_percent"]
        status = "OK"
        
        if cpu >= cpu_warn or mem >= mem_warn:
            status = "⚠ ALERT" if (cpu >= 95 or mem >= 95) else "! WARN"
            alert_count += 1
        
        table.append([
            hostname,
            status,
            f"{data['uptime_days']}d",
            f"{cpu}%",
            f"{mem}%",
        ])
    
    headers = ["Device", "Status", "Uptime", "CPU", "Memory"]
    print("\n" + tabulate(table, headers=headers, tablefmt="grid"))
    return 1 if alert_count > 0 else 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory.yaml",
        help="Nornir inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "-g", "--group",
        default="all",
        help="Device group to query (default: all)",
    )
    parser.add_argument(
        "--cpu-warn",
        type=int,
        default=80,
        help="CPU threshold %% for warning (default: 80)",
    )
    parser.add_argument(
        "--mem-warn",
        type=int,
        default=85,
        help="Memory threshold %% for warning (default: 85)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.group != "all":
            nr = nr.filter(F(groups__contains=args.group))
        
        logger.info(f"Gathering health metrics from {len(nr.inventory.hosts)} devices")
        results = nr.run(task=gather_health_metrics)
        
        exit_code = print_report(results, args.cpu_warn, args.mem_warn)
        sys.exit(exit_code)
        
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()