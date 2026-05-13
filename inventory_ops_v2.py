```python
"""
Device Log Analyzer

Aggregates and analyzes system logs from network devices to identify errors,
warnings, and patterns. Searches for critical events like interface downs,
BGP failures, authentication errors, and resource exhaustion.

Usage:
    python device_log_analyzer.py --group routers --max-lines 200
    python device_log_analyzer.py --device core-1 --pattern "BGP" --output json
    python device_log_analyzer.py --group all --severity error --output table

Prerequisites:
    - Nornir configured with YAML inventory (config.yaml in working directory)
    - Network devices reachable via SSH with netmiko support
    - Devices must support 'show log' or equivalent command
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logger = logging.getLogger(__name__)

SEVERITY_MAPPING = {"critical": 0, "error": 1, "warning": 2, "info": 3}

CRITICAL_PATTERNS = {
    "bgp": r"(?i)(bgp|border\s+gateway).*(?:down|failed|closed|reset)",
    "interface": r"(?i)(?:interface|port|link).*(?:down|disabled|error)",
    "memory": r"(?i)(?:memory|heap).*(?:error|exhausted|failed)",
    "cpu": r"(?i)cpu.*(?:\d{2,3}%|utilization|overload)",
    "auth": r"(?i)(?:authentication|login).*(?:failed|denied|error)",
}


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def get_device_logs(task: Task, max_lines: int = 100) -> Result:
    """Retrieve system logs from device using netmiko."""
    try:
        cmd_map = {
            "cisco_ios": f"show logging | tail {max_lines}",
            "cisco_xr": f"show logging | tail {max_lines}",
            "arista_eos": f"show log | tail {max_lines}",
            "juniper_junos": "show log messages | last 100",
            "default": "show log",
        }
        command = cmd_map.get(task.host.platform, cmd_map["default"])
        
        result = task.run(netmiko_send_command, command_string=command)
        return result
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to retrieve logs - {e}")
        return Result(host=task.host, failed=True, exception=e)


def analyze_logs(
    logs: str,
    search_pattern: Optional[str] = None,
    min_severity: str = "info",
) -> Dict:
    """Analyze log content for errors and patterns."""
    lines = [l.strip() for l in logs.split("\n") if l.strip()]
    
    analysis = {
        "total_entries": len(lines),
        "timestamp": datetime.now().isoformat(),
        "critical_patterns": {},
        "custom_matches": [],
        "summary": "",
    }
    
    for pattern_name, pattern_regex in CRITICAL_PATTERNS.items():
        matches = [l for l in lines if re.search(pattern_regex, l)]
        if matches:
            analysis["critical_patterns"][pattern_name] = {
                "count": len(matches),
                "samples": matches[:3],
            }
    
    if search_pattern:
        try:
            regex = re.compile(search_pattern, re.IGNORECASE)
            analysis["custom_matches"] = [l for l in lines if regex.search(l)][:10]
        except re.error as e:
            logger.error(f"Invalid regex pattern: {e}")
    
    critical_count = sum(
        v["count"] for v in analysis["critical_patterns"].values()
    )
    if critical_count > 0:
        analysis["summary"] = f"CRITICAL: {critical_count} critical event(s) found"
    elif analysis["custom_matches"]:
        analysis["summary"] = f"INFO: {len(analysis['custom_matches'])} pattern matches found"
    else:
        analysis["summary"] = "OK: No critical events detected"
    
    return analysis


def format_table_output(results: List[Dict]) -> str:
    """Format results as ASCII table."""
    lines = [
        f"{'Device':<20} {'Status':<12} {'Critical':<10} {'Total Logs':<10}",
        "-" * 52,
    ]
    
    for result in results:
        device = result.get("device", "unknown")
        status = "CRITICAL" if result["critical_patterns"] else "OK"
        critical = sum(
            v["count"] for v in result["critical_patterns"].values()
        )
        total = result.get("total_entries", 0)
        
        lines.append(
            f"{device:<20} {status:<12} {critical:<10} {total:<10}"
        )
    
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze device logs for errors and critical patterns"
    )
    parser.add_argument("--group", help="Device group from inventory")
    parser.add_argument("--device", help="Specific device hostname")
    parser.add_argument(
        "--pattern",
        help="Custom regex pattern to search logs",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=100,
        help="Maximum log lines to retrieve (default: 100)",
    )
    parser.add_argument(
        "--severity",
        choices=list(SEVERITY_MAPPING.keys()),
        default="info",
        help="Minimum severity level to report",
    )
    parser.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    
    args = parser.parse_args()
    configure_logging(args.verbose)
    
    try:
        nr = InitNornir(config_file="config.yaml")
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return 1
    
    if args.device:
        nr = nr.filter(name=args.device)
    elif args.group:
        nr = nr.filter(group=args.group)
    
    if not nr.inventory.hosts:
        logger.error("No devices matched filter criteria")
        return 1
    
    logger.info(f"Analyzing logs from {len(nr.inventory.hosts)} device(s)")
    results = nr.run(task=get_device_logs, max_lines=args.max_lines)
    
    analysis_results = []
    for device_name, task_result in results.items():
        if task_result[0].failed:
            logger.warning(f"{device_name}: Log retrieval failed")
            analysis_results.append({
                "device": device_name,
                "total_entries": 0,
                "critical_patterns": {},
                "summary": "FAILED: Unable to retrieve logs",
            })
        else:
            logs = task_result[0].result
            analysis = analyze_logs(
                logs,
                search_pattern=args.pattern,
                min_severity=args.severity,
            )
            analysis["device"] = device_name
            analysis_results.append(analysis)
    
    if args.output == "json":
        print(json.dumps(analysis_results, indent=2))
    else:
        print(format_table_output(analysis_results))
        for result in analysis_results:
            if result["critical_patterns"]:
                print(f"\n{result['device']}:")
                for pattern, data in result["critical_patterns"].items():
                    print(f"  {pattern}: {data['count']} occurrence(s)")
    
    critical_devices = sum(
        1 for r in analysis_results if r["critical_patterns"]
    )
    if critical_devices > 0:
        logger.warning(f"{critical_devices} device(s) with critical events")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
```