Device Health and Metrics Collector using Nornir.

Purpose:
    Collects system health metrics from network devices including uptime,
    memory usage, and system information. Generates a comprehensive health
    report for monitoring and baseline establishment.

Usage:
    python device_health_check.py --hosts all --output health_report.txt
    python device_health_check.py --hosts r1,r2 --verbose

Prerequisites:
    - Nornir installed and configured
    - Inventory file (config.yaml) with device definitions
    - Device credentials configured (SSH keys or username/password)
    - Netmiko support for target device types
    - SSH connectivity to all target devices
"""

import argparse
import logging
import sys
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import netmiko_send_command
from nornir.core.filter import F


logger = logging.getLogger(__name__)


def extract_metrics(task: Task, command: str) -> Result:
    """Execute command and extract basic metrics from output."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string=command,
            name=f"Collect metrics on {task.host.name}"
        )
        
        if result.failed:
            return Result(
                host=task.host,
                result={"status": "failed", "reason": "command execution failed"},
                failed=True
            )
        
        output = result[0].result
        metrics = {
            "status": "healthy",
            "command": command,
            "output_length": len(output),
            "uptime_line": None
        }
        
        for line in output.split("\n"):
            if "uptime" in line.lower():
                metrics["uptime_line"] = line.strip()
                break
        
        task.host["metrics"] = metrics
        
        return Result(
            host=task.host,
            result=metrics,
            failed=False
        )
    
    except Exception as e:
        logger.exception(f"Error collecting metrics from {task.host.name}")
        return Result(
            host=task.host,
            result={"status": "error", "exception": str(e)},
            failed=True
        )


def generate_report(nr, output_file: str = None) -> None:
    """Generate health report from collected metrics."""
    lines = ["=" * 70, "Device Health Report", "=" * 70, ""]
    
    healthy, failed, total = 0, 0, len(nr.inventory.hosts)
    
    for host_name, host in nr.inventory.hosts.items():
        metrics = host.get("metrics", {})
        status = metrics.get("status", "unknown")
        
        lines.append(
            f"{host_name:20} | Status: {status:10} | {metrics.get('uptime_line', 'N/A')}"
        )
        
        if status == "healthy":
            healthy += 1
        else:
            failed += 1
    
    lines.extend([
        "",
        "=" * 70,
        f"Summary: {healthy}/{total} healthy, {failed}/{total} failed",
        "=" * 70
    ])
    
    report = "\n".join(lines)
    print(report)
    
    if output_file:
        with open(output_file, "w") as f:
            f.write(report)
        logger.info(f"Report saved to {output_file}")


def main() -> int:
    """Execute device health check."""
    parser = argparse.ArgumentParser(
        description="Monitor device health metrics using Nornir"
    )
    parser.add_argument(
        "--hosts",
        default="all",
        help="Comma-separated host names or 'all'"
    )
    parser.add_argument(
        "--output",
        help="Output file for report"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    try:
        nr = InitNornir(config_file="config.yaml")
    except Exception as e:
        logger.error(f"Failed to load Nornir config: {e}")
        return 1
    
    if args.hosts != "all":
        hosts = [h.strip() for h in args.hosts.split(",")]
        nr = nr.filter(F(name__in=hosts))
    
    if not nr.inventory.hosts:
        logger.error("No hosts matched filter")
        return 1
    
    logger.info(f"Collecting metrics from {len(nr.inventory.hosts)} device(s)")
    
    try:
        results = nr.run(
            task=extract_metrics,
            command="show version",
            num_workers=10
        )
        generate_report(nr, args.output)
        return 0 if not results.failed_hosts else 1
    except Exception as e:
        logger.exception("Execution failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())