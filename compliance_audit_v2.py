```python
"""
Configuration Change Audit - Detect and Report Configuration Drifts

Compares current device configurations against baseline/golden configurations
to identify unauthorized changes and configuration drift. Useful for compliance
verification and change management.

Usage:
    python config_change_audit.py --inventory hosts.yaml --baseline baselines/
    python config_change_audit.py --inventory hosts.yaml --baseline baselines/ --device router1
    python config_change_audit.py --inventory hosts.yaml --baseline baselines/ --show-diff

Prerequisites:
    - Nornir installed with netmiko plugin
    - Baseline configurations stored locally (baseline_dir/device_name.conf)
    - Inventory in YAML format with device definitions
    - SSH connectivity to all network devices
"""

import argparse
import logging
import os
import difflib
from pathlib import Path
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.exceptions import NornirExecutionException


logger = logging.getLogger(__name__)


def setup_logging(debug=False):
    """Configure logging output."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )


def get_running_config(task):
    """Retrieve running configuration from device via netmiko."""
    from nornir_netmiko.tasks import netmiko_send_command
    
    try:
        result = task.run(netmiko_send_command, command_string="show running-config")
        return result.result
    except Exception as e:
        logger.error(f"Failed to retrieve config from {task.host.name}: {e}")
        return None


def load_baseline_config(baseline_dir, device_name):
    """Load baseline configuration file for device."""
    config_file = Path(baseline_dir) / f"{device_name}.conf"
    if config_file.exists():
        try:
            return config_file.read_text()
        except Exception as e:
            logger.error(f"Failed to read baseline for {device_name}: {e}")
            return None
    logger.warning(f"No baseline found for {device_name}")
    return None


def generate_diff(current, baseline):
    """Generate unified diff between current and baseline configs."""
    if not baseline:
        return None
    
    current_lines = current.splitlines(keepends=True)
    baseline_lines = baseline.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        baseline_lines,
        current_lines,
        fromfile='baseline',
        tofile='current',
        lineterm=''
    ))
    
    return diff if diff else None


def audit_device_config(task, baseline_dir, show_diff=False):
    """Audit device configuration against baseline."""
    logger.info(f"Auditing {task.host.name}")
    
    current_config = get_running_config(task)
    if not current_config:
        return {
            "host": task.host.name,
            "status": "error",
            "message": "Failed to retrieve config"
        }
    
    baseline_config = load_baseline_config(baseline_dir, task.host.name)
    if not baseline_config:
        return {
            "host": task.host.name,
            "status": "no_baseline",
            "message": "No baseline configuration available"
        }
    
    diff = generate_diff(current_config, baseline_config)
    
    return {
        "host": task.host.name,
        "status": "drift" if diff else "compliant",
        "diff_lines": len(diff) if diff else 0,
        "diff_output": diff if show_diff and diff else None
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Audit device configurations for drift from baseline"
    )
    parser.add_argument("--inventory", required=True, help="Nornir inventory file path")
    parser.add_argument("--baseline", required=True, help="Directory containing baseline configs")
    parser.add_argument("--username", required=True, help="Device username")
    parser.add_argument("--password", required=True, help="Device password")
    parser.add_argument("--device", help="Audit specific device by name")
    parser.add_argument("--group", help="Audit devices in specific group")
    parser.add_argument("--show-diff", action="store_true", help="Display line-by-line diffs")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    setup_logging(args.debug)
    
    if not os.path.isdir(args.baseline):
        logger.error(f"Baseline directory not found: {args.baseline}")
        return 1
    
    try:
        nr = InitNornir(config_file=args.inventory)
        nr.inventory.defaults.username = args.username
        nr.inventory.defaults.password = args.password
        
        if args.device:
            nr = nr.filter(F(name=args.device))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        
        results = nr.run(
            task=audit_device_config,
            baseline_dir=args.baseline,
            show_diff=args.show_diff
        )
        
        print("\n" + "="*50)
        print("CONFIGURATION AUDIT RESULTS")
        print("="*50 + "\n")
        
        compliant_count = 0
        drift_count = 0
        error_count = 0
        
        for device_name in sorted(results.keys()):
            result = results[device_name][0].result
            status = result["status"]
            host = result["host"]
            
            if status == "compliant":
                print(f"✓ {host}: Compliant with baseline")
                compliant_count += 1
            elif status == "drift":
                print(f"✗ {host}: Configuration drift detected ({result['diff_lines']} changes)")
                drift_count += 1
                if result["diff_output"]:
                    for line in result["diff_output"]:
                        print(f"  {line.rstrip()}")
            elif status == "no_baseline":
                print(f"? {host}: {result['message']}")
                error_count += 1
            elif status == "error":
                print(f"✗ {host}: {result['message']}")
                error_count += 1
        
        print("\n" + "="*50)
        print(f"Compliant: {compliant_count} | Drift: {drift_count} | Errors: {error_count}")
        print("="*50 + "\n")
        
        return 0 if drift_count == 0 and error_count == 0 else 1
        
    except NornirExecutionException as e:
        logger.error(f"Nornir execution error: {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("Audit interrupted")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=args.debug)
        return 1


if __name__ == "__main__":
    exit(main())
```