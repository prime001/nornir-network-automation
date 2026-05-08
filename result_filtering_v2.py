```python
"""
Result Filtering and Report Generation for Nornir Task Execution

This script executes network tasks across a device inventory and provides
flexible filtering of results based on device properties, task status, and
output patterns. Useful for targeting specific devices, identifying failures,
and generating compliance reports.

Usage:
    python result_filtering.py --task "show_version" --group "core"
    python result_filtering.py --task "show_bgp" --status failed --format json
    python result_filtering.py --task "show_interfaces" --pattern "up" --devices router1 router2

Prerequisites:
    - Nornir installed with netmiko/paramiko drivers
    - Inventory file (hosts.yaml) with device definitions
    - Network connectivity to target devices
    - Appropriate credentials (via environment or inventory)
"""

import logging
import argparse
import json
import re
from pathlib import Path
from typing import Optional
from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def execute_task(nornir_obj, task_name: str, command: str):
    """Execute network command across inventory."""
    logger.info(f"Executing task: {task_name} with command: {command}")
    
    def task(task):
        return task.run(
            netmiko_send_command,
            command_string=command,
            name=task_name
        )
    
    return nornir_obj.run(task=task)


def filter_results(results, status: Optional[str] = None, 
                  pattern: Optional[str] = None,
                  exclude_hosts: Optional[list] = None):
    """Filter nornir results based on status and output pattern."""
    filtered = {}
    
    for host, result in results.items():
        if exclude_hosts and host in exclude_hosts:
            continue
        
        task_result = result[0] if result else None
        if not task_result:
            continue
        
        # Filter by status
        if status:
            if status == "failed" and task_result.ok:
                continue
            elif status == "passed" and not task_result.ok:
                continue
        
        # Filter by output pattern
        if pattern:
            output = str(task_result.result)
            if not re.search(pattern, output, re.IGNORECASE):
                continue
        
        filtered[host] = result
    
    return filtered


def format_output(filtered_results, format_type: str = "table"):
    """Format results for display."""
    if format_type == "json":
        output = {}
        for host, result in filtered_results.items():
            task_result = result[0]
            output[host] = {
                "status": "passed" if task_result.ok else "failed",
                "output": str(task_result.result),
                "exception": str(task_result.exception) if task_result.exception else None
            }
        return json.dumps(output, indent=2)
    
    elif format_type == "csv":
        lines = ["hostname,status,output"]
        for host, result in filtered_results.items():
            task_result = result[0]
            status = "passed" if task_result.ok else "failed"
            output = str(task_result.result).replace(",", ";").replace("\n", " ")
            lines.append(f"{host},{status},\"{output}\"")
        return "\n".join(lines)
    
    else:  # table format
        output = []
        output.append(f"{'Hostname':<20} {'Status':<10} {'Output':<50}")
        output.append("-" * 80)
        for host, result in filtered_results.items():
            task_result = result[0]
            status = "PASS" if task_result.ok else "FAIL"
            result_text = str(task_result.result)[:47] + "..." if len(str(task_result.result)) > 50 else str(task_result.result)
            output.append(f"{host:<20} {status:<10} {result_text:<50}")
        return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--task", required=True, help="Task name for execution"
    )
    parser.add_argument(
        "--command", required=True, help="Network command to execute"
    )
    parser.add_argument(
        "--group", help="Filter by device group"
    )
    parser.add_argument(
        "--devices", nargs="+", help="Specific devices to target"
    )
    parser.add_argument(
        "--status", choices=["passed", "failed"],
        help="Filter results by execution status"
    )
    parser.add_argument(
        "--pattern", help="Filter output by regex pattern"
    )
    parser.add_argument(
        "--format", choices=["table", "json", "csv"], default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--inventory", default="inventory",
        help="Path to nornir inventory directory"
    )
    
    args = parser.parse_args()
    
    try:
        # Initialize Nornir
        logger.info(f"Loading inventory from {args.inventory}")
        nornir_obj = InitNornir(config_file=f"{args.inventory}/config.yaml")
        
        # Apply device filters
        if args.devices:
            nornir_obj = nornir_obj.filter(F(name__in=args.devices))
        elif args.group:
            nornir_obj = nornir_obj.filter(F(groups__contains=args.group))
        
        logger.info(f"Targeting {len(nornir_obj.inventory.hosts)} device(s)")
        
        # Execute task
        results = execute_task(nornir_obj, args.task, args.command)
        
        # Filter results
        filtered = filter_results(
            results,
            status=args.status,
            pattern=args.pattern
        )
        
        logger.info(f"Results after filtering: {len(filtered)} host(s)")
        
        # Format and output
        output = format_output(filtered, args.format)
        print(output)
        
        # Summary
        passed = sum(1 for h, r in filtered.items() if r[0].ok)
        failed = len(filtered) - passed
        logger.info(f"Summary - Passed: {passed}, Failed: {failed}")
        
    except Exception as e:
        logger.error(f"Error during execution: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
```