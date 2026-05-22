```python
"""
Configuration Drift Detection Tool

Detects configuration differences between running and startup configurations
on network devices. Useful for identifying manual changes made outside
of configuration management systems.

Usage:
    python config_drift_detector.py --device core-router-1 --username admin --password secret
    python config_drift_detector.py --group core --username admin --password secret

Prerequisites:
    - Nornir installed with netmiko plugin
    - Inventory file with device definitions
    - Network devices accessible via SSH
    - Netmiko support for target device types

Output:
    - Console report with drift summary
    - Log file with detailed information
    - Exit code: 0 (no drift), 1 (drift detected), 2 (error)
"""

import logging
import argparse
import sys
from difflib import unified_diff

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("config_drift.log")
    ]
)
logger = logging.getLogger(__name__)


def detect_drift(task: Task) -> Result:
    """
    Compare running and startup configurations to detect drift.
    
    Args:
        task: Nornir task object
        
    Returns:
        Result containing drift detection status and differences
    """
    try:
        # Retrieve running configuration
        running_result = task.run(
            netmiko_send_command,
            command_string="show running-config"
        )
        running_config = running_result.result
        
        # Retrieve startup configuration
        startup_result = task.run(
            netmiko_send_command,
            command_string="show startup-config"
        )
        startup_config = startup_result.result
        
        # Split configs into lines for comparison
        running_lines = running_config.splitlines(keepends=True)
        startup_lines = startup_config.splitlines(keepends=True)
        
        # Generate unified diff
        diff_lines = list(unified_diff(
            startup_lines,
            running_lines,
            fromfile="startup-config",
            tofile="running-config",
            lineterm=""
        ))
        
        drift_detected = len(diff_lines) > 0
        
        return Result(
            host=task.host,
            result={
                "drift_detected": drift_detected,
                "diff_count": len(diff_lines),
                "diff": "\n".join(diff_lines) if diff_lines else "No differences"
            }
        )
        
    except Exception as e:
        logger.error(f"Drift detection failed for {task.host}: {e}")
        return Result(
            host=task.host,
            failed=True,
            exception=e
        )


def main():
    """Main entry point with argument parsing and execution."""
    parser = argparse.ArgumentParser(
        description="Detect configuration drift between running and startup configs"
    )
    parser.add_argument(
        "--device",
        help="Target specific device"
    )
    parser.add_argument(
        "--group",
        help="Target device group"
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Device authentication username"
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Device authentication password"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Nornir inventory file"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # Initialize Nornir
        nr = InitNornir(config_file=args.inventory)
        
        # Apply filters
        if args.device:
            nr = nr.filter(F(name=args.device))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        else:
            logger.error("Must specify --device or --group")
            return 2
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter")
            return 2
        
        # Set credentials on filtered hosts
        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password
        
        # Execute drift detection
        logger.info(f"Checking {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=detect_drift)
        
        # Process and display results
        drift_detected_count = 0
        error_count = 0
        
        print(f"\n{'='*70}")
        print("Configuration Drift Detection Report")
        print(f"{'='*70}\n")
        
        for host_name, multi_result in results.items():
            result = multi_result[0]
            
            if result.failed:
                logger.error(f"{host_name}: {result.exception}")
                error_count += 1
                print(f"✗ {host_name}: ERROR - {result.exception}")
            elif result.result["drift_detected"]:
                drift_detected_count += 1
                diff_count = result.result["diff_count"]
                logger.warning(f"{host_name}: Drift detected ({diff_count} lines)")
                print(f"⚠ {host_name}: DRIFT DETECTED ({diff_count} differences)")
                
                # Show first few diff lines
                diff_text = result.result["diff"]
                diff_preview = "\n  ".join(diff_text.split("\n")[:10])
                if len(diff_text.split("\n")) > 10:
                    diff_preview += "\n  ..."
                print(f"  {diff_preview}\n")
            else:
                logger.info(f"{host_name}: No drift")
                print(f"✓ {host_name}: No drift detected")
        
        # Summary
        print(f"\n{'='*70}")
        print("Summary")
        print(f"{'='*70}")
        print(f"Total devices: {len(nr.inventory.hosts)}")
        print(f"Drift detected: {drift_detected_count}")
        print(f"Errors: {error_count}")
        
        return 1 if drift_detected_count > 0 else 0
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
```