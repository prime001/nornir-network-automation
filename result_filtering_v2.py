```python
"""
Nornir Task Result Filter and Reporter

Executes tasks across network devices and filters/analyzes results based on
device criteria, execution status, and output patterns. Generates detailed
reports in multiple formats for network validation and troubleshooting.

Usage:
    python 051_task_result_filter.py --task "show version" --format json
    python 051_task_result_filter.py --task "show interfaces" --device-type ios
    python 051_task_result_filter.py --devices router1,router2 --task "show ip route"

Prerequisites:
    - Nornir installed and configured with inventory
    - Network connectivity to all target devices
    - Device credentials configured in inventory
    - Netmiko support for target device platforms
"""

import json
import logging
import argparse
import sys
from typing import Dict, Any, Optional
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command
from nornir.core.filter import F


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ResultFilter:
    """Filters and analyzes Nornir task execution results with chainable methods."""
    
    def __init__(self, results: Dict):
        self.results = results
        self.filtered = results.copy()
    
    def by_status(self, status: str) -> 'ResultFilter':
        """Filter results by execution status (ok/failed)."""
        self.filtered = {
            name: res for name, res in self.filtered.items()
            if (status == "ok" and not res.failed) or (status == "failed" and res.failed)
        }
        return self
    
    def by_device_type(self, device_type: str) -> 'ResultFilter':
        """Filter results by device platform."""
        filtered = {}
        for name, res in self.filtered.items():
            host = res.host if hasattr(res, 'host') else None
            if host and hasattr(host, 'platform') and host.platform == device_type:
                filtered[name] = res
        self.filtered = filtered
        return self
    
    def by_pattern(self, pattern: str) -> 'ResultFilter':
        """Filter results containing specific text pattern (regex)."""
        import re
        filtered = {}
        for name, res in self.filtered.items():
            try:
                output = self._extract_output(res)
                if re.search(pattern, output, re.IGNORECASE):
                    filtered[name] = res
            except Exception:
                pass
        self.filtered = filtered
        return self
    
    @staticmethod
    def _extract_output(result) -> str:
        """Extract text output from result object."""
        if hasattr(result, 'result'):
            return str(result.result)
        return str(result)
    
    def get(self) -> Dict:
        """Return filtered results."""
        return self.filtered


def run_command_task(task: Task, command: str) -> Result:
    """Execute command via Netmiko."""
    return task.run(
        netmiko_send_command,
        command_string=command
    )


def generate_text_report(results: Dict, title: str = "Task Results") -> str:
    """Generate human-readable text report."""
    lines = [
        "=" * 70,
        title,
        "=" * 70,
        f"Total Devices: {len(results)}",
    ]
    
    ok_count = sum(1 for r in results.values() if not r.failed)
    failed_count = len(results) - ok_count
    lines.append(f"Successful: {ok_count} | Failed: {failed_count}")
    
    if len(results) > 0:
        lines.append(f"Success Rate: {(ok_count/len(results)*100):.1f}%")
    lines.append("=" * 70)
    
    for device, result in sorted(results.items()):
        status = "✓" if not result.failed else "✗"
        lines.append(f"\n[{status}] {device}")
        
        try:
            output = ResultFilter._extract_output(result)
            if output:
                output_lines = output.split('\n')[:5]
                for line in output_lines:
                    lines.append(f"    {line}")
                if len(output.split('\n')) > 5:
                    lines.append(f"    ... ({len(output.split(chr(10))) - 5} more lines)")
        except Exception as e:
            lines.append(f"    Error: {e}")
    
    return "\n".join(lines)


def generate_json_report(results: Dict) -> str:
    """Generate JSON format report."""
    report = {
        "summary": {
            "total": len(results),
            "successful": sum(1 for r in results.values() if not r.failed),
            "failed": sum(1 for r in results.values() if r.failed),
        },
        "devices": {}
    }
    
    for device, result in sorted(results.items()):
        try:
            output = ResultFilter._extract_output(result)
            report["devices"][device] = {
                "status": "ok" if not result.failed else "failed",
                "output": output[:500]
            }
        except Exception as e:
            report["devices"][device] = {
                "status": "error",
                "error": str(e)
            }
    
    return json.dumps(report, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Filter and analyze Nornir task execution results"
    )
    parser.add_argument("--task", required=True, help="Command to execute on devices")
    parser.add_argument("--devices", help="Comma-separated device list (default: all)")
    parser.add_argument("--device-type", help="Filter by device platform (ios, eos, etc.)")
    parser.add_argument("--status", choices=["ok", "failed"], help="Filter by execution status")
    parser.add_argument("--pattern", help="Filter by output pattern (regex)")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("--output", help="Write to file instead of stdout")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        logger.info("Initializing Nornir")
        nr = InitNornir()
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
            logger.info(f"Targeting {len(nr.inventory.hosts)} specified devices")
        
        logger.info(f"Executing task: {args.task}")
        results = nr.run(task=run_command_task, command=args.task)
        
        logger.info("Applying filters to results")
        filters = ResultFilter(results)
        if args.status:
            filters.by_status(args.status)
        if args.device_type:
            filters.by_device_type(args.device_type)
        if args.pattern:
            filters.by_pattern(args.pattern)
        
        filtered = filters.get()
        logger.info(f"Filtered results: {len(filtered)} devices")
        
        if args.format == "json":
            report = generate_json_report(filtered)
        else:
            report = generate_text_report(filtered)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
            logger.info(f"Report written to {args.output}")
        else:
            print(report)
        
        return 0
    
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```