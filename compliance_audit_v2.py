```python
"""
Device Reachability & Response Time Audit

Audits network device availability and measures response times across inventory.
Useful for SLA compliance verification and operational health assessment.

Usage:
    python device_reachability_audit.py --inventory inventory.yaml --username admin
    python device_reachability_audit.py --timeout 10 --output audit_results.json

Prerequisites:
    - Nornir installed (pip install nornir netmiko)
    - Inventory file with device definitions
    - Device credentials via --username/--password or environment variables
    - SSH/Telnet access to devices
    - Python 3.7+

Output:
    JSON or CSV report showing per-device availability, response time, and
    connection status. Identifies unreachable devices for troubleshooting.
"""

import logging
import argparse
import json
import csv
import time
from datetime import datetime
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_device_reachability(task: Task, timeout: int = 10) -> Result:
    """Test device reachability and measure response time."""
    start_time = time.time()
    
    try:
        task.run(
            netmiko_send_command,
            command_string="show version",
            use_textfsm=False,
        )
        response_time = time.time() - start_time
        
        return Result(
            host=task.host,
            result={
                "device": task.host.name,
                "status": "reachable",
                "response_time_ms": round(response_time * 1000, 2),
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        response_time = time.time() - start_time
        logger.warning(f"Device {task.host.name} unreachable: {str(e)[:50]}")
        
        return Result(
            host=task.host,
            failed=True,
            result={
                "device": task.host.name,
                "status": "unreachable",
                "response_time_ms": round(response_time * 1000, 2),
                "error": str(e)[:100],
                "timestamp": datetime.now().isoformat(),
            }
        )


def format_json_output(audit_results: list, indent: int = 2) -> str:
    """Format audit results as JSON."""
    return json.dumps(audit_results, indent=indent)


def format_csv_output(audit_results: list) -> str:
    """Format audit results as CSV."""
    if not audit_results:
        return "No results to report."
    
    fieldnames = ["device", "status", "response_time_ms", "timestamp", "error"]
    output_lines = []
    writer_obj = None
    
    import io
    csv_buffer = io.StringIO()
    writer_obj = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer_obj.writeheader()
    
    for result in audit_results:
        row = {f: result.get(f, "") for f in fieldnames}
        writer_obj.writerow(row)
    
    return csv_buffer.getvalue()


def print_summary(audit_results: list) -> None:
    """Print audit summary statistics."""
    total = len(audit_results)
    reachable = sum(1 for r in audit_results if r["status"] == "reachable")
    unreachable = total - reachable
    
    if reachable > 0:
        avg_response = sum(
            r["response_time_ms"] for r in audit_results 
            if r["status"] == "reachable"
        ) / reachable
    else:
        avg_response = 0
    
    print("\n" + "="*60)
    print("REACHABILITY AUDIT SUMMARY")
    print("="*60)
    print(f"Total Devices:      {total}")
    print(f"Reachable:          {reachable} ({100*reachable//total if total else 0}%)")
    print(f"Unreachable:        {unreachable} ({100*unreachable//total if total else 0}%)")
    print(f"Avg Response Time:  {avg_response:.2f}ms")
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Audit device reachability and response times"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "--username",
        help="Device username for authentication"
    )
    parser.add_argument(
        "--password",
        help="Device password for authentication"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Connection timeout in seconds (default: 10)"
    )
    parser.add_argument(
        "--filter",
        help="Filter devices by name (glob pattern)"
    )
    parser.add_argument(
        "--output",
        help="Output file path (optional)"
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json)"
    )
    
    args = parser.parse_args()
    
    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)
        
        if args.filter:
            nr = nr.filter(name=args.filter)
            logger.info(f"Filtered to {len(nr.inventory.hosts)} devices")
        
        if args.username:
            nr.inventory.defaults.username = args.username
        if args.password:
            nr.inventory.defaults.password = args.password
        
        logger.info(f"Starting reachability audit on {len(nr.inventory.hosts)} devices")
        
        results = nr.run(task=test_device_reachability, timeout=args.timeout)
        
        audit_results = []
        for host, task_results in results.items():
            for task_result in task_results:
                audit_results.append(task_result.result)
        
        audit_results.sort(key=lambda x: x["device"])
        
        if args.format == "json":
            output = format_json_output(audit_results)
        else:
            output = format_csv_output(audit_results)
        
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            logger.info(f"Audit results written to {args.output}")
        else:
            print(output)
        
        print_summary(audit_results)
        
    except Exception as e:
        logger.error(f"Audit failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```