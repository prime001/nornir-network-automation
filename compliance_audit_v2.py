```python
"""
Device Facts Collector - Network Automation Portfolio Script

Purpose:
    Collects and reports device facts (OS version, uptime, serial number, model)
    from network devices using nornir and NAPALM. Useful for inventory audits,
    capacity planning, and firmware management tracking.

Usage:
    python device_facts.py -i inventory/ [-d "device1,device2"] [-f json]

Prerequisites:
    - nornir and napalm installed
    - Inventory directory with hosts.yaml and defaults.yaml configured
    - Device SSH credentials accessible via environment or inventory
    - NAPALM driver support for target device types

Output:
    Generates device facts report in text, JSON, or CSV format.
"""

import argparse
import json
import logging
from pathlib import Path
from nornir import InitNornir
from nornir.plugins.tasks.networking import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_device_facts(task):
    """Retrieve device facts using NAPALM get_facts."""
    task.run(task=napalm_get, getters=["facts"])


def collect_facts(nr):
    """Execute facts collection across inventory."""
    results = nr.run(task=get_device_facts)
    facts_data = {}

    for host, result in results.items():
        if result.failed:
            facts_data[host] = {"error": str(result[0].exception)}
            logger.warning(f"Failed to collect facts from {host}")
        else:
            try:
                facts = result[0].result["facts"]
                facts_data[host] = {
                    "vendor": facts.get("vendor", "N/A"),
                    "model": facts.get("model", "N/A"),
                    "os_version": facts.get("os_version", "N/A"),
                    "serial_number": facts.get("serial_number", "N/A"),
                    "uptime_seconds": facts.get("uptime_seconds", 0),
                    "hostname": facts.get("hostname", host),
                }
            except (KeyError, TypeError) as e:
                facts_data[host] = {"error": f"Parse error: {str(e)}"}
                logger.warning(f"Error parsing facts from {host}: {e}")

    return facts_data


def format_text(facts_data):
    """Format facts as human-readable text."""
    lines = ["\nDevice Facts Report", "=" * 70]
    for host, data in facts_data.items():
        lines.append(f"\n{host}")
        if "error" in data:
            lines.append(f"  ERROR: {data['error']}")
        else:
            for key, value in data.items():
                if key != "hostname":
                    label = key.replace("_", " ").title()
                    lines.append(f"  {label}: {value}")
    return "\n".join(lines)


def format_json(facts_data):
    """Format facts as JSON."""
    return json.dumps(facts_data, indent=2)


def format_csv(facts_data):
    """Format facts as CSV."""
    headers = ["Host", "Vendor", "Model", "OS Version", "Serial Number"]
    lines = [",".join(headers)]

    for host, data in facts_data.items():
        if "error" in data:
            lines.append(f"{host},ERROR,{data['error']},,,")
        else:
            lines.append(
                f"{host},{data['vendor']},{data['model']},"
                f"{data['os_version']},{data['serial_number']}"
            )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Collect device facts from network devices via NAPALM"
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory",
        help="Path to nornir inventory directory (default: inventory)"
    )
    parser.add_argument(
        "-d", "--devices",
        help="Comma-separated list of target devices (all if omitted)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path (stdout if omitted)"
    )

    args = parser.parse_args()

    inv_path = Path(args.inventory)
    if not inv_path.exists():
        logger.error(f"Inventory path not found: {args.inventory}")
        return 1

    try:
        nr = InitNornir(config_file=str(inv_path / "config.yaml"))
    except Exception as e:
        logger.error(f"Failed to initialize nornir: {e}")
        return 1

    if args.devices:
        device_list = [d.strip() for d in args.devices.split(",")]
        nr = nr.filter(name__in=device_list)

    if not nr.inventory.hosts:
        logger.error("No devices found in inventory")
        return 1

    logger.info(f"Collecting facts from {len(nr.inventory.hosts)} device(s)")

    try:
        facts_data = collect_facts(nr)
    except Exception as e:
        logger.error(f"Task execution failed: {e}")
        return 1

    if args.format == "json":
        output = format_json(facts_data)
    elif args.format == "csv":
        output = format_csv(facts_data)
    else:
        output = format_text(facts_data)

    if args.output:
        try:
            Path(args.output).write_text(output)
            logger.info(f"Report written to {args.output}")
        except IOError as e:
            logger.error(f"Failed to write output file: {e}")
            return 1
    else:
        print(output)

    return 0


if __name__ == "__main__":
    exit(main())
```