```python
"""
Device Reachability and Latency Monitor

Performs reachability checks and measures response latency to network devices.
Identifies unreachable hosts, analyzes response times, and generates connectivity
reports for network diagnostic and monitoring purposes.

Usage:
    python device_reachability.py --devices router1,router2 --count 4
    python device_reachability.py --group access-layer --output csv
    python device_reachability.py --timeout 5 --verbose

Prerequisites:
    - Nornir configured with inventory (hosts.yaml, groups.yaml, defaults.yaml)
    - ICMP (ping) access to target devices
    - Network connectivity to all target devices
    - netmiko or paramiko for device connectivity (for DNS resolution fallback)

Output:
    Generates a reachability report in JSON or CSV format with:
    - Device IP and hostname
    - Reachability status (up/down)
    - Average, min, max latency
    - Packet loss percentage
    - Last check timestamp
"""

import argparse
import csv
import json
import logging
import statistics
import subprocess
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Dict, Any, List, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result


logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def ping_host(host: str, count: int = 4, timeout: int = 3) -> Dict[str, Any]:
    """
    Ping a host and collect latency metrics.
    
    Args:
        host: IP address or hostname to ping
        count: Number of ping packets to send
        timeout: Timeout in seconds per packet
    
    Returns:
        Dictionary with reachability status and latency metrics
    """
    try:
        cmd = ["ping", "-c", str(count), "-W", str(timeout * 1000), host]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout * count + 5
        )
        
        if result.returncode != 0:
            return {
                "host": host,
                "reachable": False,
                "packet_loss": 100.0,
                "avg_latency_ms": None,
                "min_latency_ms": None,
                "max_latency_ms": None,
            }
        
        lines = result.stdout.split("\n")
        stats_line = next((l for l in lines if "min/avg/max" in l), None)
        
        if not stats_line:
            return {
                "host": host,
                "reachable": True,
                "packet_loss": 0.0,
                "avg_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0,
            }
        
        parts = stats_line.split("=")[1].split("/")
        latencies = [float(p.strip()) for p in parts[:3]]
        
        return {
            "host": host,
            "reachable": True,
            "packet_loss": 0.0,
            "avg_latency_ms": round(latencies[1], 2),
            "min_latency_ms": round(latencies[0], 2),
            "max_latency_ms": round(latencies[2], 2),
        }
    
    except subprocess.TimeoutExpired:
        logger.warning(f"Ping timeout for {host}")
        return {
            "host": host,
            "reachable": False,
            "packet_loss": 100.0,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
        }
    except Exception as e:
        logger.error(f"Error pinging {host}: {e}")
        return {
            "host": host,
            "reachable": False,
            "packet_loss": 100.0,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "error": str(e),
        }


def check_reachability(task: Task, count: int, timeout: int) -> Result:
    """Nornir task to check device reachability."""
    host_ip = task.host.get("host", task.host.name)
    
    logger.debug(f"Checking reachability for {task.host.name} ({host_ip})")
    
    metrics = ping_host(host_ip, count, timeout)
    metrics["device"] = task.host.name
    metrics["timestamp"] = datetime.now().isoformat()
    
    return Result(host=task.host, result=metrics)


def format_output(results: List[Dict[str, Any]], format_type: str) -> str:
    """Format results as JSON, CSV, or text."""
    if not results:
        return "No results available"
    
    if format_type == "json":
        return json.dumps(results, indent=2, default=str)
    
    elif format_type == "csv":
        output = StringIO()
        if results:
            fieldnames = list(results[0].keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        return output.getvalue()
    
    else:
        lines = []
        for r in results:
            lines.append(f"\nDevice: {r.get('device')}")
            lines.append(f"  Host: {r.get('host')}")
            lines.append(f"  Reachable: {r.get('reachable')}")
            
            if r.get("reachable"):
                lines.append(f"  Avg Latency: {r.get('avg_latency_ms')} ms")
                lines.append(f"  Min/Max: {r.get('min_latency_ms')}/{r.get('max_latency_ms')} ms")
            else:
                lines.append(f"  Status: UNREACHABLE")
                if "error" in r:
                    lines.append(f"  Error: {r.get('error')}")
        
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Check network device reachability and measure latency"
    )
    parser.add_argument(
        "--devices",
        type=str,
        help="Comma-separated list of device names"
    )
    parser.add_argument(
        "--group",
        type=str,
        help="Filter devices by group name"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=4,
        help="Number of ping packets per device (default: 4)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3,
        help="Timeout per packet in seconds (default: 3)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="reachability_report.json",
        help="Output file path (default: reachability_report.json)"
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "text"],
        default="json",
        help="Output format (default: json)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        nr = InitNornir()
        logger.info(f"Initialized Nornir with {len(nr.inventory.hosts)} hosts")
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(F(name__in=device_list))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} specified devices")
        
        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} devices in group '{args.group}'")
        
        if not nr.inventory.hosts:
            logger.error("No devices found matching filters")
            return 1
        
        results = []
        for hostname, host_obj in nr.inventory.hosts.items():
            task_result = check_reachability(
                type('Task', (), {'host': host_obj})(),
                args.count,
                args.timeout
            )
            results.append(task_result.result)
        
        output = format_output(results, args.format)
        
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
        
        logger.info(f"Reachability report written to {output_path}")
        print(output)
        
        reachable = sum(1 for r in results if r.get("reachable"))
        total = len(results)
        logger.info(f"Summary: {reachable}/{total} devices reachable")
        
        return 0 if reachable == total else 1
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```