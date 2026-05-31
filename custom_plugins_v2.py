```python
"""
Device Software Version Auditor

Purpose:
    Audits network device software versions for compliance.
    Compares actual device OS versions against a baseline specification.
    Flags non-compliant devices for remediation.

Usage:
    python software_version_auditor.py -d all -u admin -p password -b versions.json
    python software_version_auditor.py -d prod -u admin -p password -b versions.json --report

Prerequisites:
    - Nornir inventory file (hosts and groups defined in YAML)
    - Device credentials with read access
    - Baseline file: JSON with expected versions per device or group
    - NAPALM driver support for your devices
    - Python 3.7+, nornir>=2.5, napalm>=2.5

Baseline JSON format:
    {
        "device_name": "15.2(4)M10",
        "group_name": "15.2(4)M*",
        "vendor": "15.2+"
    }
"""

import argparse
import json
import logging
from pathlib import Path
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm import napalm_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def audit_version(task, baseline):
    """
    Fetch device version and compare against baseline.
    
    Args:
        task: Nornir task
        baseline: Dict mapping device/group names to expected versions
    
    Returns:
        Dict with version info and compliance status
    """
    try:
        result = task.run(napalm_get, getters=["facts"])
        facts = result[0].result["facts"]
        
        device_name = task.host.name
        group_name = task.host.groups[0].name if task.host.groups else None
        expected_version = baseline.get(device_name) or baseline.get(group_name)
        actual_version = facts.get("os_version", "unknown")
        
        return {
            "device": device_name,
            "group": group_name,
            "vendor": facts.get("vendor", "unknown"),
            "model": facts.get("model", "unknown"),
            "actual_version": actual_version,
            "expected_version": expected_version,
            "compliant": actual_version == expected_version
            if expected_version else None,
            "uptime": facts.get("uptime_seconds", 0),
        }
    except Exception as e:
        logger.error(f"{task.host.name}: {e}")
        return {"device": task.host.name, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-d", "--device", default="all",
                        help="Target device or group")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument("-b", "--baseline", required=True,
                        help="Baseline JSON file")
    parser.add_argument("-i", "--inventory", default="inventory.yaml",
                        help="Inventory YAML file")
    parser.add_argument("--report", action="store_true",
                        help="Generate detailed report")
    parser.add_argument("--no-fail", action="store_true",
                        help="Exit 0 even if non-compliant")
    
    args = parser.parse_args()
    
    try:
        baseline_path = Path(args.baseline)
        if not baseline_path.exists():
            logger.error(f"Baseline file not found: {args.baseline}")
            return 1
        
        with open(baseline_path) as f:
            baseline = json.load(f)
        
        logger.info(f"Loaded {len(baseline)} baseline entries")
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in baseline: {e}")
        return 1
    except Exception as e:
        logger.error(f"Failed to load baseline: {e}")
        return 1
    
    try:
        nr = InitNornir(
            inventory={
                "plugin": "SimpleInventory",
                "options": {"host_file": args.inventory}
            }
        )
        
        if args.device != "all":
            nr = nr.filter(
                F(groups__contains=args.device) | F(name=args.device)
            )
        
        if not nr.inventory.hosts:
            logger.warning("No matching devices found")
            return 1
        
        logger.info(f"Auditing {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=audit_version, baseline=baseline)
        
        compliant = []
        non_compliant = []
        errors = []
        no_baseline = []
        
        for host, multi_result in results.items():
            if multi_result.failed:
                errors.append((host, "Task execution failed"))
                continue
            
            data = multi_result[0].result
            
            if "error" in data:
                errors.append((host, data["error"]))
            elif data["compliant"] is None:
                no_baseline.append(data)
            elif data["compliant"]:
                compliant.append(data)
            else:
                non_compliant.append(data)
        
        print("\n" + "="*70)
        print("VERSION COMPLIANCE AUDIT REPORT")
        print("="*70 + "\n")
        
        if compliant:
            print(f"✓ COMPLIANT ({len(compliant)}):")
            for dev in compliant:
                print(f"  {dev['device']:20s} {dev['actual_version']}")
        
        if no_baseline:
            print(f"\n? NO BASELINE ({len(no_baseline)}):")
            for dev in no_baseline:
                print(f"  {dev['device']:20s} {dev['actual_version']}")
        
        if non_compliant:
            print(f"\n✗ NON-COMPLIANT ({len(non_compliant)}):")
            for dev in non_compliant:
                print(f"  {dev['device']:20s} {dev['actual_version']}")
                print(f"    Expected: {dev['expected_version']}")
        
        if errors:
            print(f"\n! ERRORS ({len(errors)}):")
            for dev, err in errors:
                print(f"  {dev}: {err}")
        
        print("\n" + "="*70)
        print(f"Summary: {len(compliant)} compliant, "
              f"{len(non_compliant)} non-compliant, "
              f"{len(no_baseline)} no baseline, "
              f"{len(errors)} errors")
        print("="*70 + "\n")
        
        if non_compliant and not args.no_fail:
            logger.warning("Non-compliant devices detected")
            return 1
        
        return 0
        
    except Exception as e:
        logger.error(f"Audit failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```