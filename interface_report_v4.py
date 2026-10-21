```python
"""
Device Health Monitor - Gathers CPU, memory, uptime, and interface metrics from network devices.

Purpose:
    Monitors the operational health of network devices by collecting system metrics
    and interface statistics. Useful for capacity planning and troubleshooting.

Usage:
    python device_health_monitor.py --hosts all --threshold 80
    python device_health_monitor.py --hosts rtr01,rtr02 --metric cpu --format json

Prerequisites:
    - Nornir with netmiko/napalm plugin
    - Valid inventory.yaml with device credentials
    - Devices supporting 'show version', 'show system resources', or napalm getters
    - Network connectivity to all target devices

Arguments:
    --hosts: Comma-separated hostnames or 'all' (default: all)
    --threshold: CPU/memory threshold % for alerts (default: 80)
    --metric: Specific metric to collect: cpu, memory, uptime, all (default: all)
    --format: Output format: text, json (default: text)
"""

import argparse
import json
import logging
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the script."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def gather_health_metrics(task: Task) -> Result:
    """Gather CPU, memory, and uptime metrics from a device using netmiko."""
    device = task.host
    metrics = {"hostname": device.name, "device_type": device.platform}

    try:
        if device.platform == "cisco_ios":
            output = task.run(
                netmiko_send_command,
                command_string="show version",
                use_textfsm=False,
            )
            raw = output[0].result
            metrics["uptime_raw"] = raw.split("uptime is")[1].split("Processor")[0].strip() if "uptime is" in raw else "unknown"

            output = task.run(
                netmiko_send_command,
                command_string="show processes cpu | include CPU",
                use_textfsm=False,
            )
            cpu_line = output[0].result.strip()
            try:
                cpu_usage = float(cpu_line.split()[-2].rstrip("%"))
                metrics["cpu_usage_percent"] = cpu_usage
            except (ValueError, IndexError):
                metrics["cpu_usage_percent"] = None

            output = task.run(
                netmiko_send_command,
                command_string="show memory statistics | include Processor",
                use_textfsm=False,
            )
            mem_line = output[0].result.strip()
            try:
                mem_usage = float(mem_line.split()[-2].rstrip("%"))
                metrics["memory_usage_percent"] = mem_usage
            except (ValueError, IndexError):
                metrics["memory_usage_percent"] = None

        elif device.platform == "arista_eos":
            output = task.run(
                netmiko_send_command,
                command_string="show version",
                use_textfsm=False,
            )
            raw = output[0].result
            if "uptime" in raw:
                metrics["uptime_raw"] = raw.split("uptime")[1].split("\n")[0].strip()

        metrics["status"] = "success"

    except Exception as exc:
        metrics["status"] = "failed"
        metrics["error"] = str(exc)

    return Result(host=device, result=metrics)


def format_output(data: Dict[str, Any], output_format: str = "text") -> str:
    """Format collected metrics for display."""
    if output_format == "json":
        return json.dumps(data, indent=2)

    lines = []
    for hostname, metrics in data.items():
        lines.append(f"\n{'='*60}")
        lines.append(f"Device: {hostname}")
        lines.append(f"{'='*60}")

        if metrics.get("status") == "failed":
            lines.append(f"ERROR: {metrics.get('error', 'Unknown error')}")
            continue

        lines.append(f"  Platform: {metrics.get('device_type', 'unknown')}")
        if metrics.get("cpu_usage_percent") is not None:
            cpu = metrics["cpu_usage_percent"]
            alert = " ⚠️  HIGH" if cpu > 80 else ""
            lines.append(f"  CPU Usage: {cpu}%{alert}")
        if metrics.get("memory_usage_percent") is not None:
            mem = metrics["memory_usage_percent"]
            alert = " ⚠️  HIGH" if mem > 80 else ""
            lines.append(f"  Memory Usage: {mem}%{alert}")
        if metrics.get("uptime_raw"):
            lines.append(f"  Uptime: {metrics['uptime_raw']}")

    return "\n".join(lines)


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor network device health metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hosts",
        default="all",
        help="Comma-separated hostnames or 'all' (default: all)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=80,
        help="CPU/memory threshold %% for alerts (default: 80)",
    )
    parser.add_argument(
        "--metric",
        choices=["cpu", "memory", "uptime", "all"],
        default="all",
        help="Metric to collect (default: all)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    try:
        nr = InitNornir(config_file="config.yaml")
        logger.info("Nornir inventory loaded successfully")

        if args.hosts != "all":
            host_list = [h.strip() for h in args.hosts.split(",")]
            nr = nr.filter(name__in=host_list)

        logger.info(f"Running health check on {len(nr.inventory.hosts)} devices")
        results = nr.run(task=gather_health_metrics)

        output_data = {
            host: results[host][0].result for host in results.keys()
        }

        print(format_output(output_data, args.format))
        logger.info("Health check completed successfully")

    except Exception as exc:
        logger.error(f"Execution failed: {exc}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```