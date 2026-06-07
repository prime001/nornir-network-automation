```python
"""
Multi-device command execution with drift detection and reporting.

Executes a show command on multiple network devices and identifies
configuration drift by comparing outputs across the network.

Usage:
    python device_drift_detection.py \\
        --command "show version" \\
        --devices router1 router2 router3
    
    python device_drift_detection.py \\
        --command "show interfaces brief" \\
        --verbose

Prerequisites:
    - Nornir configured with inventory and credentials
    - netmiko installed for device connectivity
    - Devices must support the specified show command
"""

import argparse
import hashlib
import json
import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def execute_show_command(task: Task, command: str) -> Result:
    """Execute show command on device."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string=command,
            use_timing=False
        )
        return result
    except Exception as e:
        logger.error(f"Command failed on {task.host.name}: {e}")
        raise


def hash_output(output: str) -> str:
    """Generate hash of command output for comparison."""
    normalized = output.strip().lower()
    return hashlib.md5(normalized.encode()).hexdigest()


def group_by_output(outputs: Dict[str, str]) -> Dict[str, List[str]]:
    """Group devices by identical command output."""
    groups = defaultdict(list)
    for device, output in outputs.items():
        output_hash = hash_output(output)
        groups[output_hash].append(device)
    return dict(groups)


def format_report(
    groups: Dict[str, List[str]],
    outputs: Dict[str, str],
    command: str
) -> str:
    """Generate formatted drift analysis report."""
    lines = [
        "\n" + "=" * 75,
        "CONFIGURATION DRIFT ANALYSIS",
        "=" * 75,
        f"Command: {command}",
        f"Devices scanned: {len(outputs)}",
        f"Unique outputs: {len(groups)}",
        "=" * 75 + "\n"
    ]
    
    if len(groups) == 1:
        devices = list(groups.values())[0]
        lines.append("✓ NO DRIFT DETECTED")
        lines.append(f"  All {len(devices)} devices have identical output\n")
    else:
        lines.append("⚠ DRIFT DETECTED\n")
        for idx, (_, devices) in enumerate(groups.items(), 1):
            lines.append(f"Output Group {idx} ({len(devices)} device{'s' if len(devices) > 1 else ''}):")
            for device in sorted(devices):
                lines.append(f"  • {device}")
            lines.append("")
    
    lines.append("=" * 75 + "\n")
    return "\n".join(lines)


def show_sample_outputs(
    groups: Dict[str, List[str]],
    outputs: Dict[str, str],
    max_length: int = 300
) -> str:
    """Display sample outputs from each group."""
    lines = ["SAMPLE OUTPUTS:\n"]
    
    for idx, (output_hash, devices) in enumerate(groups.items(), 1):
        sample_device = devices[0]
        sample_output = outputs[sample_device]
        truncated = sample_output[:max_length]
        
        lines.append(f"Group {idx} (from {sample_device}):")
        lines.append("-" * 75)
        lines.append(truncated)
        if len(sample_output) > max_length:
            lines.append(f"... [output truncated, {len(sample_output)} chars total]")
        lines.append("")
    
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect configuration drift across network devices"
    )
    parser.add_argument(
        "--command",
        required=True,
        help="Show command to execute on devices"
    )
    parser.add_argument(
        "--devices",
        nargs="*",
        default=None,
        help="Specific device names to target (all devices if not specified)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Display sample outputs from each group"
    )
    parser.add_argument(
        "--inventory",
        default="inventory",
        help="Path to Nornir inventory"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(inventory=args.inventory)
        
        if args.devices:
            nr = nr.filter(name__in=args.devices)
            logger.info(f"Targeting {len(nr.inventory.hosts)} specified device(s)")
        else:
            logger.info(f"Targeting all {len(nr.inventory.hosts)} device(s)")
        
        if not nr.inventory.hosts:
            logger.error("No devices found in inventory")
            return 1
        
        logger.info(f"Executing: {args.command}")
        results = nr.run(
            task=execute_show_command,
            command=args.command
        )
        
        outputs = {}
        failed_devices = []
        
        for device, task_result in results.items():
            if task_result[0].ok:
                outputs[device] = task_result[0].result
            else:
                failed_devices.append(device)
        
        if failed_devices:
            logger.warning(f"Failed on {len(failed_devices)} device(s): {', '.join(failed_devices)}")
        
        if not outputs:
            logger.error("No successful command executions")
            return 2
        
        groups = group_by_output(outputs)
        report = format_report(groups, outputs, args.command)
        print(report)
        
        if args.verbose and len(groups) > 1:
            sample = show_sample_outputs(groups, outputs)
            print(sample)
        
        has_drift = len(groups) > 1
        return 1 if has_drift else 0
        
    except Exception as e:
        logger.error(f"Script execution failed: {e}", exc_info=True)
        return 2


if __name__ == "__main__":
    exit(main())
```