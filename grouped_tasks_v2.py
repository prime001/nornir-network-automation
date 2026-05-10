```python
"""
Device Facts Gathering and Change Detection Tool

Purpose:
    Collects system facts from network devices using NAPALM and detects changes
    against a baseline snapshot. Useful for tracking hardware changes, OS upgrades,
    and device configuration drift.

Usage:
    python device_facts.py --username admin --password secret --collect
    python device_facts.py --username admin --password secret --compare baseline.json
    python device_facts.py --host router1 --username admin --password secret --collect

Prerequisites:
    - Nornir configured with inventory (config.yaml)
    - NAPALM installed (pip install napalm)
    - Network devices accessible via SSH
    - Valid SSH credentials
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def gather_device_facts(task: Task) -> Result:
    """Retrieve device facts using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result.get("facts", {})

        return Result(
            host=task.host,
            result={
                "hostname": facts.get("hostname"),
                "os_version": facts.get("os_version"),
                "serial_number": facts.get("serial_number"),
                "model": facts.get("model"),
                "vendor": facts.get("vendor"),
                "uptime": facts.get("uptime"),
                "interface_count": facts.get("interface_count"),
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Failed to retrieve facts from {task.host}: {e}")
        return Result(host=task.host, failed=True, exception=e)


def detect_changes(
    current_facts: Dict[str, Any],
    baseline_facts: Dict[str, Any]
) -> Dict[str, Any]:
    """Detect changes between current and baseline facts."""
    changes = {}
    tracked_fields = ["os_version", "serial_number", "model", "uptime"]

    for device, current in current_facts.items():
        if device not in baseline_facts:
            changes[device] = {"status": "NEW_DEVICE"}
            continue

        baseline = baseline_facts[device]
        device_changes = {}

        for field in tracked_fields:
            current_val = current.get(field)
            baseline_val = baseline.get(field)

            if current_val != baseline_val:
                device_changes[field] = {
                    "previous": baseline_val,
                    "current": current_val,
                }

        if device_changes:
            changes[device] = device_changes

    removed_devices = set(baseline_facts.keys()) - set(current_facts.keys())
    for device in removed_devices:
        changes[device] = {"status": "REMOVED"}

    return changes


def save_facts(facts: Dict[str, Any], filename: str) -> None:
    """Save facts to JSON file."""
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    with open(filename, "w") as f:
        json.dump(facts, f, indent=2)
    logger.info(f"Facts saved to {filename}")


def load_facts(filename: str) -> Optional[Dict[str, Any]]:
    """Load facts from JSON file."""
    try:
        with open(filename) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"File not found: {filename}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Collect device facts and detect changes"
    )
    parser.add_argument("--host", help="Single device to query")
    parser.add_argument("--group", help="Device group from inventory")
    parser.add_argument("--username", required=True, help="SSH username")
    parser.add_argument("--password", required=True, help="SSH password")
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Collect facts from devices"
    )
    parser.add_argument(
        "--compare",
        metavar="BASELINE_FILE",
        help="Compare against baseline and report changes"
    )
    parser.add_argument(
        "--output",
        default="device_facts_current.json",
        help="Output file for collected facts"
    )

    args = parser.parse_args()

    if not (args.collect or args.compare):
        parser.error("Use --collect to gather facts or --compare to detect changes")

    try:
        nr = InitNornir(config_file="config.yaml")

        if args.host:
            nr = nr.filter(name=args.host)
        elif args.group:
            nr = nr.filter(group=args.group)

        if not nr.inventory.hosts:
            logger.error("No matching devices in inventory")
            return 1

        logger.info(f"Processing {len(nr.inventory.hosts)} device(s)")

        results = nr.run(task=gather_device_facts)

        collected_facts = {}
        for device_name, task_result in results.items():
            if task_result[0].failed:
                logger.warning(f"Failed to collect facts from {device_name}")
            else:
                collected_facts[device_name] = task_result[0].result

        if args.collect:
            save_facts(collected_facts, args.output)

        if args.compare:
            baseline = load_facts(args.compare)
            if not baseline:
                return 1

            changes = detect_changes(collected_facts, baseline)

            if changes:
                logger.warning(f"Changes detected in {len(changes)} device(s):")
                print(json.dumps(changes, indent=2))
            else:
                logger.info("No changes detected")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
```