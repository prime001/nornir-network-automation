```python
"""
Device Configuration Audit and Compliance Checker

Purpose:
    Audits network device configurations against compliance rules and generates
    a detailed report of deviations, missing configurations, and risk items.
    Useful for identifying unauthorized changes, missing standards, and
    configuration drift across multi-vendor device fleets.

Usage:
    python 031_config_audit.py --inventory inventory.yaml --group router \
        --rules compliance_rules.json --output audit_report.json

Prerequisites:
    - Nornir inventory file (YAML format)
    - Compliance rules file (JSON format, see sample below)
    - Network connectivity to all devices
    - Credentials via environment variables or SSH keys

Compliance Rules Format (JSON):
    {
        "required_lines": ["logging level info", "no shut"],
        "forbidden_lines": ["service unsecured-http"],
        "interface_checks": {
            "Ethernet1/1": {"speed": "100", "mtu": "1500"}
        }
    }
"""

import json
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.tasks.networking import netmiko_send_command

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_compliance_rules(rules_file: str) -> Dict[str, Any]:
    """Load compliance rules from JSON file."""
    try:
        with open(rules_file, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Rules file not found: {rules_file}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in rules file: {e}")
        raise


def check_device_config(task: Task, rules: Dict[str, Any]) -> Result:
    """
    Retrieve device config and audit against compliance rules.
    """
    device_name = task.host.name
    results = {
        "device": device_name,
        "compliant": True,
        "findings": []
    }

    try:
        # Retrieve running configuration
        config_result = task.run(
            netmiko_send_command,
            command_string="show running-config",
            use_textfsm=False,
        )

        config_text = config_result[0].result
        config_lines = [line.strip() for line in config_text.split('\n')]

        # Check required lines
        for required in rules.get("required_lines", []):
            found = any(required.lower() in line.lower() for line in config_lines)
            if not found:
                results["compliant"] = False
                results["findings"].append({
                    "type": "missing_required",
                    "item": required,
                    "severity": "high"
                })

        # Check forbidden lines
        for forbidden in rules.get("forbidden_lines", []):
            found = any(forbidden.lower() in line.lower() for line in config_lines)
            if found:
                results["compliant"] = False
                results["findings"].append({
                    "type": "forbidden_line_found",
                    "item": forbidden,
                    "severity": "critical"
                })

        # Check interface-specific rules
        if_rules = rules.get("interface_checks", {})
        if if_rules:
            int_result = task.run(
                netmiko_send_command,
                command_string="show interfaces",
                use_textfsm=False,
            )
            int_text = int_result[0].result

            for interface, checks in if_rules.items():
                for check_key, check_value in checks.items():
                    pattern = f"{interface}.*{check_key}.*{check_value}"
                    found = any(
                        check_key.lower() in line.lower()
                        and check_value.lower() in line.lower()
                        for line in int_text.split('\n')
                        if interface.lower() in line.lower()
                    )
                    if not found:
                        results["compliant"] = False
                        results["findings"].append({
                            "type": "interface_compliance",
                            "interface": interface,
                            "check": f"{check_key}={check_value}",
                            "severity": "medium"
                        })

        logger.info(
            f"{device_name}: "
            f"{'COMPLIANT' if results['compliant'] else 'NON-COMPLIANT'} "
            f"({len(results['findings'])} findings)"
        )

    except Exception as e:
        logger.error(f"Error auditing {device_name}: {e}")
        results["compliant"] = False
        results["findings"].append({
            "type": "audit_error",
            "error": str(e),
            "severity": "critical"
        })

    return Result(host=task.host, result=results)


def run_audit(nr, rules: Dict[str, Any], group_filter: str = None) -> Dict[str, Any]:
    """Execute config audit across filtered device group."""
    if group_filter:
        nr = nr.filter(F(groups__contains=group_filter))

    logger.info(f"Starting audit on {len(nr.inventory.hosts)} devices")

    results = nr.run(task=check_device_config, rules=rules)

    audit_summary = {
        "total_devices": len(nr.inventory.hosts),
        "compliant_devices": 0,
        "non_compliant_devices": 0,
        "devices": {}
    }

    for hostname, task_result in results.items():
        device_result = task_result[0].result
        audit_summary["devices"][hostname] = device_result

        if device_result["compliant"]:
            audit_summary["compliant_devices"] += 1
        else:
            audit_summary["non_compliant_devices"] += 1

    audit_summary["compliance_percentage"] = (
        audit_summary["compliant_devices"] / audit_summary["total_devices"] * 100
        if audit_summary["total_devices"] > 0 else 0
    )

    return audit_summary


def main():
    parser = argparse.ArgumentParser(
        description="Network device configuration compliance auditor"
    )
    parser.add_argument(
        "--inventory",
        required=True,
        help="Path to Nornir inventory YAML file"
    )
    parser.add_argument(
        "--rules",
        required=True,
        help="Path to compliance rules JSON file"
    )
    parser.add_argument(
        "--group",
        help="Filter audit to specific device group"
    )
    parser.add_argument(
        "--output",
        help="Output file for audit results (JSON)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # Initialize Nornir
        nr = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded inventory: {len(nr.inventory.hosts)} hosts")

        # Load compliance rules
        rules = load_compliance_rules(args.rules)
        logger.info(f"Loaded compliance rules with {len(rules)} rule sets")

        # Run audit
        audit_results = run_audit(nr, rules, args.group)

        # Output results
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(json.dumps(audit_results, indent=2))
            logger.info(f"Audit results saved to {args.output}")
        else:
            print(json.dumps(audit_results, indent=2))

        # Summary
        print(f"\n{'='*60}")
        print(f"Audit Summary")
        print(f"{'='*60}")
        print(f"Total Devices: {audit_results['total_devices']}")
        print(f"Compliant: {audit_results['compliant_devices']}")
        print(f"Non-Compliant: {audit_results['non_compliant_devices']}")
        print(f"Compliance Rate: {audit_results['compliance_percentage']:.1f}%")
        print(f"{'='*60}\n")

        # Exit with appropriate code
        exit(0 if audit_results["non_compliant_devices"] == 0 else 1)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        exit(2)


if __name__ == "__main__":
    main()
```