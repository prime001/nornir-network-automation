```python
"""
Network Device Health Check with Result Filtering

Purpose:
    Connects to network devices, executes multiple diagnostic commands
    (interface status, routing table, system info), filters results for
    warnings/errors, and generates a health report with actionable insights.

Usage:
    python health_check_filter.py --inventory inventory.yaml \
        --device "core-router-01" --format json

Prerequisites:
    - nornir installed (pip install nornir)
    - netmiko and napalm backends configured
    - Inventory file in YAML format with device definitions
    - SSH/CLI access to target devices with credentials

Features:
    - Multi-command execution with timeout handling
    - Severity-based result filtering
    - JSON and text output formats
    - Detailed logging and error handling
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_diagnostics(task: Task) -> Result:
    """Execute diagnostic commands on the device."""
    commands = {
        "version": "show version",
        "interfaces": "show interfaces brief",
        "routing": "show ip route summary",
        "bgp": "show bgp summary",
    }

    results = {}
    for key, cmd in commands.items():
        try:
            r = task.run(
                netmiko_send_command,
                command_string=cmd,
                timeout_short=10
            )
            results[key] = r.result if r else ""
        except Exception as e:
            results[key] = f"COMMAND_ERROR: {str(e)}"
            logger.debug(f"{task.host}: {cmd} failed - {e}")

    return Result(host=task.host, result=results)


def parse_results(output: str, check_type: str) -> Dict[str, Any]:
    """Extract and categorize issues from command output."""
    issues = []
    warnings = []

    error_patterns = {
        "down": ("critical", "interface down"),
        "disabled": ("warning", "interface disabled"),
        "error": ("critical", "error detected"),
        "failed": ("critical", "command failed"),
        "unreachable": ("critical", "route unreachable"),
    }

    for pattern, (severity, description) in error_patterns.items():
        if pattern.lower() in output.lower():
            item = {
                "severity": severity,
                "pattern": pattern,
                "type": check_type,
            }
            if severity == "critical":
                issues.append(item)
            else:
                warnings.append(item)

    return {"issues": issues, "warnings": warnings}


def filter_device_results(
    diagnostics: Dict[str, str], threshold: int = 0
) -> Dict[str, Any]:
    """Filter and summarize diagnostic results."""
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_commands": len(diagnostics),
        "passed": 0,
        "issues": [],
        "warnings": [],
    }

    for check_type, output in diagnostics.items():
        if isinstance(output, str):
            if "COMMAND_ERROR" in output:
                summary["issues"].append({
                    "severity": "critical",
                    "check": check_type,
                    "message": output,
                })
            else:
                parsed = parse_results(output, check_type)
                summary["issues"].extend(parsed["issues"])
                summary["warnings"].extend(parsed["warnings"])

                if not parsed["issues"] and not parsed["warnings"]:
                    summary["passed"] += 1

    if threshold == 0:
        return summary

    return {
        k: v for k, v in summary.items()
        if k != "issues" or v
    }


def format_output(
    device_name: str,
    filtered: Dict[str, Any],
    output_format: str
) -> str:
    """Format filtered results for display."""
    if output_format == "json":
        return json.dumps({device_name: filtered}, indent=2)

    lines = [
        f"\n{'='*50}",
        f"Device: {device_name}",
        f"Timestamp: {filtered['timestamp']}",
        f"{'='*50}",
        f"Commands Passed: {filtered['passed']}/{filtered['total_commands']}",
    ]

    if filtered["issues"]:
        lines.append(f"\n⚠ CRITICAL ISSUES ({len(filtered['issues'])}):")
        for issue in filtered["issues"]:
            lines.append(
                f"  [{issue.get('severity', 'unknown').upper()}] "
                f"{issue.get('check', issue.get('type', 'unknown'))}"
            )

    if filtered["warnings"]:
        lines.append(f"\n⚠ WARNINGS ({len(filtered['warnings'])}):")
        for warn in filtered["warnings"][:5]:
            lines.append(
                f"  [{warn.get('severity', 'warning').upper()}] "
                f"{warn.get('pattern', 'unknown')}"
            )

    if not filtered["issues"] and not filtered["warnings"]:
        lines.append("\n✓ All checks passed - device healthy")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "--device",
        help="Filter by device name"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=0,
        help="Severity threshold: 0=all, 1=critical only (default: 0)"
    )

    args = parser.parse_args()

    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)

        if args.device:
            nr = nr.filter(F(name=args.device))
            if not nr.inventory.hosts:
                logger.error(f"Device '{args.device}' not found")
                sys.exit(1)

        logger.info(
            f"Running health checks on {len(nr.inventory.hosts)} device(s)"
        )
        results = nr.run(task=run_diagnostics)

        for device_name, task_result in results.items():
            if task_result.failed:
                logger.error(f"Health check failed for {device_name}")
                continue

            diagnostics = task_result[0].result
            filtered = filter_device_results(diagnostics, args.threshold)
            output = format_output(device_name, filtered, args.format)
            print(output)

        logger.info("Health check complete")

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```