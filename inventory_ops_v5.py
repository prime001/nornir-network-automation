```python
#!/usr/bin/env python3
"""
Multi-Device Configuration Change Detector and Auditor.

Captures device running configurations and detects changes over time. Useful
for identifying unauthorized modifications and maintaining configuration audit
trails.

Purpose:
    Monitor network devices for configuration drift and unauthorized changes.
    Generate reports comparing current configs against baselines.

Usage:
    python config_change_detector.py -i inventory.yaml --action snapshot
    python config_change_detector.py -i inventory.yaml --action compare --baseline baseline.json
    python config_change_detector.py -i inventory.yaml --action compare --baseline baseline.json -d device1,device2

Prerequisites:
    - nornir installed with netmiko plugin
    - Valid device inventory in YAML format
    - SSH access to all network devices
    - User has permission to execute 'show running-config'

Output:
    JSON files containing configuration snapshots and change reports with diff details.
"""

import logging
import argparse
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from difflib import unified_diff

from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result


logger = logging.getLogger(__name__)


def capture_config(task) -> Dict[str, Any]:
    """Capture running configuration from a single device."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string="show running-config"
        )
        config_text = result[0].result
        config_hash = hashlib.sha256(config_text.encode()).hexdigest()

        return {
            "success": True,
            "config": config_text,
            "hash": config_hash,
            "length": len(config_text),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"{task.host.name}: Failed to capture config: {str(e)}")
        return {
            "success": False,
            "error": str(e)[:150],
            "timestamp": datetime.now().isoformat(),
        }


def create_snapshot(nr, device_filter: Optional[str] = None) -> Dict[str, Any]:
    """Create a snapshot of running configurations from all devices."""
    logger.info("Creating configuration snapshot...")

    if device_filter:
        filtered_nr = nr.filter(F(name__contains=device_filter))
    else:
        filtered_nr = nr

    snapshot_data = {
        "timestamp": datetime.now().isoformat(),
        "device_count": len(filtered_nr.inventory.hosts),
        "devices": {},
    }

    results = filtered_nr.run(task=capture_config)

    for device_name, multi_result in results.items():
        if multi_result[0].result:
            snapshot_data["devices"][device_name] = multi_result[0].result

    successful = sum(1 for d in snapshot_data["devices"].values() if d.get("success"))
    logger.info(f"Snapshot created: {successful}/{len(filtered_nr.inventory.hosts)} devices")

    return snapshot_data


def compare_snapshots(baseline: Dict, current: Dict) -> Dict[str, Any]:
    """Compare baseline and current configuration snapshots."""
    logger.info("Comparing configurations...")

    report = {
        "baseline_timestamp": baseline.get("timestamp"),
        "current_timestamp": current.get("timestamp"),
        "summary": {
            "total_devices": len(current.get("devices", {})),
            "changed": 0,
            "unchanged": 0,
            "added": 0,
            "removed": 0,
            "errors": 0,
        },
        "devices": {},
    }

    baseline_devs = set(baseline.get("devices", {}).keys())
    current_devs = set(current.get("devices", {}).keys())

    added = current_devs - baseline_devs
    removed = baseline_devs - current_devs
    common = baseline_devs & current_devs

    for device in added:
        report["summary"]["added"] += 1
        report["devices"][device] = {"status": "added"}

    for device in removed:
        report["summary"]["removed"] += 1
        report["devices"][device] = {"status": "removed"}

    for device in common:
        baseline_data = baseline["devices"][device]
        current_data = current["devices"][device]

        if not baseline_data.get("success") or not current_data.get("success"):
            report["summary"]["errors"] += 1
            report["devices"][device] = {"status": "error"}
            continue

        baseline_hash = baseline_data.get("hash")
        current_hash = current_data.get("hash")

        if baseline_hash == current_hash:
            report["summary"]["unchanged"] += 1
            report["devices"][device] = {"status": "unchanged", "hash": current_hash}
        else:
            report["summary"]["changed"] += 1
            baseline_lines = baseline_data.get("config", "").splitlines()
            current_lines = current_data.get("config", "").splitlines()

            diff_lines = list(
                unified_diff(
                    baseline_lines,
                    current_lines,
                    fromfile="baseline",
                    tofile="current",
                    n=3,
                    lineterm="",
                )
            )

            report["devices"][device] = {
                "status": "changed",
                "baseline_hash": baseline_hash,
                "current_hash": current_hash,
                "diff_count": len(diff_lines),
                "diff_preview": diff_lines[:30],
            }

    logger.info(
        f"Comparison complete: {report['summary']['changed']} changed, "
        f"{report['summary']['unchanged']} unchanged"
    )

    return report


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Detect configuration changes across network devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "-i",
        "--inventory",
        required=True,
        help="Path to nornir inventory file",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["snapshot", "compare"],
        help="Action to perform",
    )
    parser.add_argument(
        "-b",
        "--baseline",
        help="Baseline snapshot file for comparison",
    )
    parser.add_argument(
        "-d",
        "--devices",
        help="Filter by device name pattern",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (default: auto-generated)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded inventory: {len(nr.inventory.hosts)} devices")

        if args.action == "snapshot":
            snapshot = create_snapshot(nr, args.devices)
            output_file = (
                args.output
                or f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            with open(output_file, "w") as f:
                json.dump(snapshot, f, indent=2)
            logger.info(f"Snapshot saved to {output_file}")
            print(f"✓ Snapshot saved: {output_file}")

        elif args.action == "compare":
            if not args.baseline:
                logger.error("--baseline required for compare action")
                return 1

            if not Path(args.baseline).exists():
                logger.error(f"Baseline file not found: {args.baseline}")
                return 1

            with open(args.baseline) as f:
                baseline = json.load(f)

            current = create_snapshot(nr, args.devices)
            report = compare_snapshots(baseline, current)

            output_file = (
                args.output
                or f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            with open(output_file, "w") as f:
                json.dump(report, f, indent=2)

            logger.info(f"Report saved to {output_file}")
            print(f"✓ Report saved: {output_file}")
            print(
                f"\nSummary: {report['summary']['changed']} changed, "
                f"{report['summary']['unchanged']} unchanged, "
                f"{report['summary']['added']} added, "
                f"{report['summary']['removed']} removed"
            )

        return 0

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```