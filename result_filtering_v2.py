```python
"""
Device Facts Inventory and Comparison Tool

Gathers device facts from network inventory and generates a comprehensive
inventory report showing device hardware, software, and uptime information.
Supports filtering by device type and comparison mode to identify
configuration drift or hardware discrepancies.

Usage:
    python device_inventory.py --hosts router1 router2 router3
    python device_inventory.py --hosts all --filter-type cisco_ios
    python device_inventory.py --hosts all --compare-mode --output inventory.csv

Prerequisites:
    - nornir configured with valid inventory in config.yaml
    - napalm driver installed
    - Device SSH/telnet credentials configured in inventory
"""

import argparse
import csv
import logging
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get_facts


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def gather_device_facts(task: Task) -> Result:
    """Gather device facts using NAPALM."""
    return task.run(task=napalm_get_facts)


def filter_facts(facts: Dict[str, Any]) -> Dict[str, str]:
    """Extract and format key device facts."""
    uptime_hours = facts.get("uptime", 0) // 3600 if facts.get("uptime") else 0
    return {
        "Hostname": facts.get("hostname", "N/A"),
        "Model": facts.get("model", "N/A"),
        "Serial": facts.get("serial_number", "N/A"),
        "OS Version": facts.get("os_version", "N/A"),
        "Uptime (hours)": str(uptime_hours),
        "Interface Count": str(facts.get("interface_count", 0)),
    }


def generate_inventory_report(
    device_facts: Dict[str, Dict[str, str]], compare_mode: bool = False
) -> None:
    """Generate and print inventory report."""
    if not device_facts:
        logger.warning("No device facts collected")
        return

    print("\n" + "=" * 120)
    print("DEVICE INVENTORY REPORT")
    print("=" * 120)

    headers = list(device_facts[next(iter(device_facts))].keys())
    col_width = 18

    header_row = "Device".ljust(col_width) + " | ".join(
        h.ljust(col_width) for h in headers
    )
    print(header_row)
    print("-" * 120)

    for device_name, facts in sorted(device_facts.items()):
        values = [device_name] + [facts.get(h, "N/A") for h in headers]
        row = " | ".join(str(v).ljust(col_width) for v in values)
        print(row)

    if compare_mode:
        print("\n" + "=" * 60)
        print("COMPARISON ANALYSIS")
        print("=" * 60)
        _analyze_drift(device_facts)

    print("=" * 120 + "\n")


def _analyze_drift(device_facts: Dict[str, Dict[str, str]]) -> None:
    """Identify hardware and software drift between devices."""
    if len(device_facts) < 2:
        print("Need at least 2 devices for comparison\n")
        return

    models = {}
    versions = {}

    for device, facts in device_facts.items():
        model = facts.get("Model", "Unknown")
        version = facts.get("OS Version", "Unknown")
        models.setdefault(model, []).append(device)
        versions.setdefault(version, []).append(device)

    if len(set(models.keys())) > 1:
        print("\n⚠ Model Discrepancies:")
        for model, devices in models.items():
            print(f"  {model}: {', '.join(sorted(devices))}")

    if len(set(versions.keys())) > 1:
        print("\n⚠ OS Version Discrepancies:")
        for version, devices in versions.items():
            print(f"  {version}: {', '.join(sorted(devices))}")

    if len(set(models.keys())) == 1 and len(set(versions.keys())) == 1:
        print("✓ No hardware or software drift detected\n")


def export_csv(device_facts: Dict[str, Dict[str, str]], filepath: str) -> None:
    """Export device facts to CSV file."""
    if not device_facts:
        return

    with open(filepath, "w", newline="") as f:
        headers = ["Device"] + list(device_facts[next(iter(device_facts))].keys())
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()

        for device in sorted(device_facts.keys()):
            row = {"Device": device}
            row.update(device_facts[device])
            writer.writerow(row)


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        default=["all"],
        help="Device hostname(s) to query (default: all)",
    )
    parser.add_argument(
        "--filter-type",
        help="Filter devices by type (e.g., cisco_ios, arista_eos)",
    )
    parser.add_argument(
        "--compare-mode",
        action="store_true",
        help="Enable comparison mode to identify drift",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Export results to CSV file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        nr = InitNornir(config_file="config.yaml")

        if args.hosts != ["all"]:
            nr = nr.filter(name__in=args.hosts)

        if args.filter_type:
            nr = nr.filter(device_type=args.filter_type)

        logger.info(f"Gathering facts from {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=gather_device_facts)

        device_facts = {}
        for device_name, task_result in results.items():
            if task_result[0].result:
                device_facts[device_name] = filter_facts(task_result[0].result)
            else:
                logger.error(f"Failed to gather facts from {device_name}")

        generate_inventory_report(device_facts, args.compare_mode)

        if args.output:
            export_csv(device_facts, args.output)
            logger.info(f"Report exported to {args.output}")

    except Exception as e:
        logger.error(f"Error during execution: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```