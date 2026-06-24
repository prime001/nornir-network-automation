```python
"""
Device Health and Performance Monitor using Nornir.

Purpose:
    Collects CPU, memory, uptime, and interface error metrics from network
    devices and generates a health report with warnings for degraded conditions.

Usage:
    python device_health_monitor.py --inventory hosts.yml --username admin
    python device_health_monitor.py --inventory hosts.yml --username admin \
        --devices router1 router2 --cpu-threshold 80

Prerequisites:
    - Nornir installed (pip install nornir)
    - Nornir inventory file with device definitions
    - Network devices with SSH connectivity and netmiko-compatible OS
    - Credentials with read-only device access
"""

import argparse
import logging
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command

logger = logging.getLogger(__name__)


def setup_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def collect_system_health(task: Task) -> Result:
    """Collect CPU, memory, and uptime metrics from device."""
    try:
        device_type = task.host.platform or "ios"

        if device_type.lower() in ["ios", "ios-xe"]:
            task.run(
                netmiko_send_command,
                command_string="show processes cpu",
                name="cpu"
            )
            task.run(
                netmiko_send_command,
                command_string="show version | include uptime",
                name="uptime"
            )
            task.run(
                netmiko_send_command,
                command_string="show memory statistics",
                name="memory"
            )
            task.run(
                netmiko_send_command,
                command_string="show interfaces | include (errors|discards)",
                name="errors"
            )

            return Result(
                host=task.host,
                result={
                    "cpu": task.results.get("cpu"),
                    "uptime": task.results.get("uptime"),
                    "memory": task.results.get("memory"),
                    "errors": task.results.get("errors")
                }
            )

        return Result(
            host=task.host,
            result={"error": f"Unsupported platform: {device_type}"},
            failed=True
        )

    except Exception as e:
        logger.error(f"Health check failed for {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={"error": str(e)},
            failed=True
        )


def print_report(results: Dict[str, Any]) -> None:
    """Generate formatted health report."""
    print("\n" + "=" * 80)
    print("DEVICE HEALTH REPORT")
    print("=" * 80)

    for device_name, task_result in results.items():
        status = "✓" if not task_result.failed else "✗"
        print(f"\n[{status}] {device_name}")
        print("-" * 80)

        if task_result.failed:
            print(f"  ERROR: {task_result[0].result.get('error', 'Unknown error')}")
            continue

        data = task_result[0].result
        print(f"  CPU Metrics:")
        print(f"    {data.get('cpu', 'N/A').strip()[:100]}")
        print(f"  Uptime:")
        print(f"    {data.get('uptime', 'N/A').strip()[:100]}")
        print(f"  Memory:")
        print(f"    {data.get('memory', 'N/A').strip()[:100]}")

        errors = data.get("errors", "")
        if errors.strip():
            print(f"  Interface Issues Detected:")
            for line in errors.strip().split("\n")[:5]:
                if line.strip():
                    print(f"    {line.strip()}")
        else:
            print("  Interface Issues: None detected")

    print("\n" + "=" * 80)


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor device health metrics across network devices"
    )
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to Nornir inventory file"
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Username for device authentication"
    )
    parser.add_argument(
        "--password",
        help="Password for device authentication"
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        help="Specific devices to monitor (all if not specified)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v or -vv)"
    )

    args = parser.parse_args()
    setup_logging(args.verbosity)

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.devices:
            nr = nr.filter(F(name__in=args.devices))

        for host in nr.inventory.hosts.values():
            host.username = args.username
            if args.password:
                host.password = args.password

        logger.info(f"Starting health check for {len(nr.inventory.hosts)} device(s)")

        results = nr.run(task=collect_system_health)

        print_report(results)

        failed_count = sum(1 for r in results.values() if r.failed)
        if failed_count == 0:
            logger.info("Health monitoring completed successfully")
        else:
            logger.warning(f"Health monitoring completed with {failed_count} failures")

    except Exception as e:
        logger.error(f"Health monitoring failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```