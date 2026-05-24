```python
"""
Network Configuration Change Validator

Validates configuration changes by capturing baseline configs, applying changes,
and verifying correctness. Supports automatic rollback on validation failure.

Usage:
    python config_validator.py --devices router1,router2 --username admin \\
        --password secret --commands "interface ge-0/0/1" "mtu 1500" \\
        --verify-pattern "mtu 1500"

Prerequisites:
    - Nornir installed with netmiko/napalm plugins
    - Network devices accessible and configured for remote access
    - Proper credentials with privilege level for config changes

Returns:
    - 0 on successful validation
    - 1 if validation fails (config may be rolled back)
    - 2 if connection/execution error occurs
"""

import argparse
import json
import logging
from datetime import datetime
from typing import Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import netmiko_send_command, netmiko_send_config
from nornir.plugins.functions.text import print_result


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)


def validate_change(task, commands: list[str], verify_pattern: str, rollback: bool = True):
    """
    Execute config change and validate against expected pattern.

    Args:
        task: Nornir task object
        commands: List of configuration commands to apply
        verify_pattern: String pattern to search for in validation check
        rollback: Rollback config if validation fails
    """
    device = task.host
    timestamp = datetime.now().isoformat()

    try:
        # Capture baseline
        baseline = task.run(
            netmiko_send_command,
            command_string="show running-config",
            name=f"baseline_{device.name}"
        )
        logger.info(f"{device.name}: Baseline config captured ({len(baseline[1].result)} bytes)")

        # Apply configuration
        task.run(
            netmiko_send_config,
            config_commands=commands,
            exit_config_mode=True,
            name=f"deploy_{device.name}"
        )
        logger.info(f"{device.name}: Configuration deployed: {commands}")

        # Verify change
        verify_output = task.run(
            netmiko_send_command,
            command_string="show running-config",
            name=f"verify_{device.name}"
        )

        if verify_pattern.lower() in verify_output[2].result.lower():
            logger.info(f"{device.name}: ✓ Verification passed (pattern found)")
            return {
                "status": "success",
                "device": device.name,
                "timestamp": timestamp,
                "commands_applied": commands,
                "validation": "pattern_found"
            }
        else:
            logger.warning(f"{device.name}: ✗ Verification failed (pattern not found)")

            if rollback:
                logger.info(f"{device.name}: Initiating rollback...")
                rollback_commands = [
                    f"no {cmd}" if not cmd.startswith("no ") else cmd.replace("no ", "")
                    for cmd in commands
                ]
                task.run(
                    netmiko_send_config,
                    config_commands=rollback_commands,
                    exit_config_mode=True,
                    name=f"rollback_{device.name}"
                )
                logger.info(f"{device.name}: Rollback completed")

            return {
                "status": "failed",
                "device": device.name,
                "timestamp": timestamp,
                "commands_applied": commands,
                "validation": "pattern_not_found",
                "rollback_executed": rollback
            }

    except Exception as e:
        logger.error(f"{device.name}: Execution error: {str(e)}")
        return {
            "status": "error",
            "device": device.name,
            "timestamp": timestamp,
            "error": str(e)
        }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--devices",
        required=True,
        help="Comma-separated device names (e.g., router1,router2)"
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Username for device authentication"
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Password for device authentication"
    )
    parser.add_argument(
        "--commands",
        nargs="+",
        required=True,
        help="Configuration commands to apply (space-separated list)"
    )
    parser.add_argument(
        "--verify-pattern",
        required=True,
        help="String pattern to verify in post-change config"
    )
    parser.add_argument(
        "--no-rollback",
        action="store_true",
        help="Disable automatic rollback on validation failure"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yml",
        help="Path to Nornir inventory file (default: inventory.yml)"
    )

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)
        devices = [d.strip() for d in args.devices.split(",")]
        nr = nr.filter(F(name__in=devices))

        if not nr.inventory.hosts:
            logger.error(f"No devices found matching: {devices}")
            return 2

        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password

        logger.info(f"Starting validation on {len(nr.inventory.hosts)} device(s)")

        results = nr.run(
            task=validate_change,
            commands=args.commands,
            verify_pattern=args.verify_pattern,
            rollback=not args.no_rollback
        )

        print_result(results)

        success_count = sum(
            1 for r in results.values()
            if isinstance(r, dict) and r.get("status") == "success"
        )
        total_count = len(results)

        logger.info(f"\nSummary: {success_count}/{total_count} devices validated successfully")

        if success_count == total_count:
            return 0
        else:
            return 1

    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 2
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return 2


if __name__ == "__main__":
    exit(main())
```