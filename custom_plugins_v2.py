```python
"""
Device Health Check - Nornir Health Metrics Collector

Gathers and reports on device health metrics including CPU, memory, uptime,
and system information across a multi-vendor network using NAPALM.

Usage:
    python device_health_check.py --inventory inventory.yml --devices all
    python device_health_check.py --devices router1,router2 --log-level DEBUG
    python device_health_check.py --devices all --format json

Prerequisites:
    - Nornir configured with device inventory
    - NAPALM installed and configured for target platforms
    - Network connectivity to managed devices
    - Valid device credentials in inventory
"""

import argparse
import json
import logging
from typing import Dict, Any, List
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def get_device_health(task: Task) -> Result:
    """Gather device facts and interface status using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["facts"])
        return result
    except Exception as e:
        logger.error(f"Failed to retrieve facts from {task.host.name}: {e}")
        return Result(host=task.host, failed=True, result={"error": str(e)})


def parse_health_data(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant health metrics from NAPALM facts output."""
    try:
        if not facts or "facts" not in facts:
            return {"error": "No facts data available"}

        fact_data = facts["facts"][0] if isinstance(facts["facts"], list) else facts["facts"]

        uptime_seconds = fact_data.get("uptime_seconds", 0)
        days = uptime_seconds // 86400
        hours = (uptime_seconds % 86400) // 3600
        minutes = (uptime_seconds % 3600) // 60

        return {
            "hostname": fact_data.get("hostname"),
            "vendor": fact_data.get("vendor"),
            "model": fact_data.get("model"),
            "serial_number": fact_data.get("serial_number"),
            "os_version": fact_data.get("os_version"),
            "uptime": f"{days}d {hours}h {minutes}m",
            "uptime_seconds": uptime_seconds,
            "fqdn": fact_data.get("fqdn"),
            "interface_count": fact_data.get("interface_count", "N/A"),
        }
    except Exception as e:
        logger.error(f"Error parsing health data: {e}")
        return {"error": str(e)}


def display_health_report(
    health_results: Dict[str, Dict[str, Any]], output_format: str = "text"
) -> None:
    """Display health check results in specified format."""
    if output_format == "json":
        print(json.dumps(health_results, indent=2))
        return

    print("\n" + "=" * 110)
    print("DEVICE HEALTH CHECK REPORT".center(110))
    print("=" * 110)

    for device_name, metrics in health_results.items():
        if "error" in metrics:
            print(f"\n[{device_name}] - ERROR: {metrics['error']}")
            continue

        print(f"\n[{device_name}]")
        print(f"  Hostname:      {metrics.get('hostname', 'N/A')}")
        print(f"  Vendor:        {metrics.get('vendor', 'N/A')}")
        print(f"  Model:         {metrics.get('model', 'N/A')}")
        print(f"  Serial:        {metrics.get('serial_number', 'N/A')}")
        print(f"  OS Version:    {metrics.get('os_version', 'N/A')}")
        print(f"  Uptime:        {metrics.get('uptime', 'N/A')}")
        print(f"  Interfaces:    {metrics.get('interface_count', 'N/A')}")

    print("\n" + "=" * 110)


def main():
    """Main entry point for health check script."""
    parser = argparse.ArgumentParser(
        description="Gather device health metrics across network inventory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python device_health_check.py --devices all
  python device_health_check.py --devices core-router-1,core-router-2
  python device_health_check.py --devices all --format json --inventory custom_inventory.yml
        """,
    )

    parser.add_argument(
        "--inventory",
        type=str,
        default="inventory.yml",
        help="Path to Nornir inventory file (default: inventory.yml)",
    )

    parser.add_argument(
        "--devices",
        type=str,
        default="all",
        help="Target device(s): 'all' or comma-separated names (default: all)",
    )

    parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()
    setup_logging(args.log_level)

    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices from inventory")

        if args.devices.lower() != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name__in=device_list)
            logger.info(f"Filtered to {len(nr.inventory.hosts)} device(s)")

        if len(nr.inventory.hosts) == 0:
            logger.warning("No devices matched the filter criteria")
            return

        logger.info("Starting health check...")
        results = nr.run(task=get_device_health)

        health_data = {}
        for device_name, task_result in results.items():
            if task_result[0].failed:
                health_data[device_name] = task_result[0].result
            else:
                health_data[device_name] = parse_health_data(task_result[0].result)

        display_health_report(health_data, output_format=args.format)
        logger.info("Health check completed")

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise


if __name__ == "__main__":
    main()
```