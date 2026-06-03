```python
"""
Device Facts and Health Report Generator.

Collects system facts and interface information from network devices using NAPALM
and generates a comprehensive health report. Useful for device audits, capacity
planning, and health monitoring.

Usage:
    python device_facts_reporter.py --inventory inventory.yaml --username admin

Prerequisites:
    - Nornir with napalm plugin
    - Network device inventory (YAML format)
    - SSH connectivity to devices
    - NAPALM-compatible drivers (Cisco IOS, Junos, Arista, etc.)

Examples:
    # Generate report for all devices
    python device_facts_reporter.py --inventory inventory.yaml --username admin

    # Target specific devices
    python device_facts_reporter.py --inventory inventory.yaml \\
        --username admin --devices r1 r2 --format json

    # Report on specific facts
    python device_facts_reporter.py --inventory inventory.yaml \\
        --username admin --facts uptime serial_number model
"""

import argparse
import json
import logging
import sys
from getpass import getpass

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_facts(task, getters):
    """Retrieve facts from device via NAPALM."""
    task.run(napalm_get, getters=getters, name="facts")


def print_text_report(results):
    """Format and print facts as text report."""
    print(f"\n{'=' * 70}")
    print(f"{'Device Facts Report':^70}")
    print(f"{'=' * 70}\n")

    failed = 0
    for hostname in sorted(results.keys()):
        task_result = results[hostname]
        if task_result.failed:
            print(f"❌ {hostname}: FAILED")
            if task_result.exception:
                print(f"   Error: {task_result.exception}\n")
            failed += 1
            continue

        facts = task_result[1].result.get("get_facts", {})
        print(f"✓ {hostname}")
        print(f"  Vendor:    {facts.get('vendor', 'N/A')}")
        print(f"  Model:     {facts.get('model', 'N/A')}")
        print(f"  OS:        {facts.get('os_version', 'N/A')}")
        print(f"  Serial:    {facts.get('serial_number', 'N/A')}")
        uptime_days = facts.get('uptime', 0) / 86400
        print(f"  Uptime:    {uptime_days:.1f} days")
        print(f"  Interfaces:{len(facts.get('interface_list', []))}")
        print()

    summary = f"Total: {len(results)} | Success: {len(results) - failed} | Failed: {failed}"
    print(f"{'=' * 70}")
    print(f"{summary:^70}\n")


def print_json_report(results):
    """Format and print facts as JSON."""
    output = {}
    for hostname, task_result in results.items():
        if task_result.failed:
            output[hostname] = {
                "status": "failed",
                "error": str(task_result.exception)
            }
        else:
            output[hostname] = task_result[1].result
    print(json.dumps(output, indent=2, default=str))


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Gather and report device facts via NAPALM"
    )
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to Nornir inventory file"
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Device username"
    )
    parser.add_argument(
        "--password",
        help="Device password (will prompt if not provided)"
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        help="Target specific devices (space-separated hostnames)"
    )
    parser.add_argument(
        "--facts",
        nargs="+",
        default=["get_facts"],
        help="NAPALM getters to retrieve (default: get_facts)"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Device connection timeout in seconds (default: 30)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)"
    )

    args = parser.parse_args()

    password = args.password or getpass("Device password: ")

    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Inventory loaded: {len(nr.inventory.hosts)} hosts")

        if args.devices:
            nr = nr.filter(F(name__in=args.devices))
            logger.info(f"Filtered to {len(nr.inventory.hosts)} specified devices")

        for host in nr.inventory.hosts.values():
            host.connection_options["netmiko"].password = password
            host.connection_options["netmiko"].extras = {
                "timeout": args.timeout
            }

        logger.info(f"Gathering facts using {args.workers} workers...")
        results = nr.run(
            task=get_facts,
            getters=args.facts,
            num_workers=args.workers
        )

        if args.format == "json":
            print_json_report(results)
        else:
            print_text_report(results)

        failed_count = sum(1 for r in results.values() if r.failed)
        if failed_count:
            sys.exit(1)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```