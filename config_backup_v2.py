```python
#!/usr/bin/env python3
"""
Device Health Check

Collects and reports health metrics (uptime, CPU, memory) from network devices
using Nornir and NAPALM. Displays device status in table or JSON format.

Usage:
    python device_health_check.py --device switch01
    python device_health_check.py --group core --format json
    python device_health_check.py --alert-threshold 80 --output health.json

Prerequisites:
    Nornir and NAPALM installed. Network devices must support NAPALM get_facts
    and get_environment getters. Inventory must be configured with device
    credentials and connection details.
"""

import argparse
import json
import logging
import sys
from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


def collect_health(task):
    """Gather device facts and environment metrics via NAPALM."""
    try:
        result = task.run(napalm_get, getters=["facts", "environment"])
        facts = result[0].result.get("facts", {})
        env = result[0].result.get("environment", {})

        cpu = env.get("cpu", [{}])[0].get("%usage", "N/A") if env.get("cpu") else "N/A"

        mem = env.get("memory", [{}])[0] if env.get("memory") else {}
        used = mem.get("used_ram", 0)
        total = mem.get("available_ram", 1)
        mem_pct = round((used / total * 100), 1) if total else "N/A"

        uptime_s = facts.get("uptime_seconds", 0)
        uptime_h = int(uptime_s) // 3600 if uptime_s else "N/A"

        return {
            "hostname": task.host.name,
            "uptime_hours": uptime_h,
            "os_version": facts.get("os_version", "N/A"),
            "cpu_percent": cpu,
            "memory_percent": mem_pct,
            "status": "OK"
        }
    except Exception as e:
        logging.error(f"Error collecting health from {task.host.name}: {e}")
        return {
            "hostname": task.host.name,
            "status": "ERROR",
            "error": str(e)
        }


def format_table(devices):
    """Format health data as ASCII table."""
    lines = [
        "Device            Uptime(h)  CPU%    Memory%  OS Version      Status"
    ]
    lines.append("-" * 75)

    for device in devices:
        if device.get("status") == "ERROR":
            lines.append(
                f"{device['hostname']:<18} ERROR - {device.get('error', 'Unknown')}"
            )
        else:
            cpu = f"{device['cpu_percent']}%" if device['cpu_percent'] != "N/A" else "N/A"
            mem = f"{device['memory_percent']}%" if device['memory_percent'] != "N/A" else "N/A"
            lines.append(
                f"{device['hostname']:<18} {str(device['uptime_hours']):<10} "
                f"{cpu:<8} {mem:<9} {str(device.get('os_version', 'N/A')):<15} "
                f"{device['status']}"
            )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Collect and report health metrics from network devices"
    )
    parser.add_argument(
        "--device",
        help="Specific device hostname to check"
    )
    parser.add_argument(
        "--group",
        help="Device group to check"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--output",
        help="Save output to file"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent workers (default: 4)"
    )

    args = parser.parse_args()

    try:
        nr = InitNornir()
        logging.info("Nornir inventory initialized")

        if args.device:
            nr = nr.filter(name=args.device)
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))

        if not nr.inventory.hosts:
            logging.error("No devices matched the specified filter")
            sys.exit(1)

        logging.info(f"Running health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=collect_health, num_workers=args.workers)

        data = [results[host][0].result for host in results.keys()]

        if args.format == "json":
            output = json.dumps(data, indent=2, default=str)
        else:
            output = format_table(data)

        print(output)

        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            logging.info(f"Output saved to {args.output}")

    except Exception as e:
        logging.error(f"Execution failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```