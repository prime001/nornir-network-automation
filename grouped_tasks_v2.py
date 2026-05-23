```python
"""
SSH Connectivity Validator for Network Devices using Nornir

Purpose:
    Validates SSH connectivity and credential configuration across network
    inventory. Useful as a pre-flight check before running automation tasks
    or for troubleshooting device accessibility issues.

Usage:
    python ssh_connectivity_validator.py --inventory inventory \
        --group spine --timeout 15 --retries 2 --output json

Prerequisites:
    - Nornir >= 3.0
    - paramiko or netmiko installed
    - Valid inventory.yaml with device credentials configured
    - Network connectivity to target devices

Features:
    - Tests SSH connectivity to each device
    - Validates device type and platform detection
    - Generates detailed connectivity reports
    - Supports device/group filtering
    - Multiple output formats (text, json)
    - Detailed error reporting for troubleshooting
"""

import argparse
import json
import logging
from datetime import datetime
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def test_ssh_connectivity(task: Task) -> Result:
    """Test SSH connectivity by executing a simple command."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string="show version",
            name="SSH Connectivity Test",
        )
        return result
    except Exception as exc:
        return Result(host=task.host, failed=True, exception=exc)


def validate_devices(nr, timeout: int) -> Dict[str, Dict[str, Any]]:
    """Execute connectivity validation across all devices in inventory."""
    validation_results = {}
    total_devices = len(nr.inventory.hosts)
    
    logger.info(f"Starting connectivity validation for {total_devices} devices")
    
    for idx, (device_name, device) in enumerate(nr.inventory.hosts.items(), 1):
        logger.debug(f"[{idx}/{total_devices}] Testing {device_name} ({device.host})")
        
        device_info = {
            "hostname": device_name,
            "ip_address": device.host,
            "port": device.port or 22,
            "username": device.username or "unknown",
            "platform": device.platform or "unknown",
            "groups": list(device.groups.keys()) if device.groups else [],
            "ssh_reachable": False,
            "timestamp": datetime.now().isoformat(),
            "error_message": None,
            "device_type": "unknown",
        }
        
        try:
            task_result = nr.run(
                task=test_ssh_connectivity,
                name=f"validate_{device_name}",
                on_failed=True,
            )
            
            host_result = task_result[device_name]
            
            if not host_result.failed:
                device_info["ssh_reachable"] = True
                output_lines = str(host_result[0].result).split("\n")
                if output_lines:
                    device_info["device_type"] = _extract_device_type(output_lines)
                logger.info(f"✓ {device_name}: SSH accessible")
            else:
                device_info["error_message"] = str(host_result[0].exception)
                logger.warning(f"✗ {device_name}: {device_info['error_message']}")
        
        except Exception as e:
            device_info["error_message"] = str(e)
            logger.error(f"✗ {device_name}: Unexpected error: {e}")
        
        validation_results[device_name] = device_info
    
    return validation_results


def _extract_device_type(output_lines: list) -> str:
    """Extract device type from show version output."""
    for line in output_lines[:10]:
        line_lower = line.lower()
        if "cisco" in line_lower:
            return "Cisco"
        elif "juniper" in line_lower or "junos" in line_lower:
            return "Juniper"
        elif "arista" in line_lower:
            return "Arista"
        elif "nokia" in line_lower or "srl" in line_lower:
            return "Nokia"
    return "Unknown"


def format_text_report(results: Dict[str, Dict[str, Any]]) -> str:
    """Generate human-readable text report."""
    accessible = sum(1 for r in results.values() if r["ssh_reachable"])
    total = len(results)
    
    report = [
        "=" * 85,
        "SSH CONNECTIVITY VALIDATION REPORT",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 85,
        f"Summary: {accessible}/{total} devices SSH accessible ({100*accessible//total}%)",
        "",
        f"{'Device':<20} {'IP Address':<18} {'Platform':<12} {'Status':<10}",
        "-" * 85,
    ]
    
    for device_name, data in sorted(results.items()):
        status = "✓ REACHABLE" if data["ssh_reachable"] else "✗ UNREACHABLE"
        report.append(
            f"{device_name:<20} {data['ip_address']:<18} "
            f"{data['platform']:<12} {status:<10}"
        )
        
        if data["error_message"]:
            report.append(f"  └─ Error: {data['error_message'][:60]}")
    
    report.append("")
    report.append("=" * 85)
    
    return "\n".join(report)


def format_json_report(results: Dict[str, Dict[str, Any]]) -> str:
    """Generate JSON report."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_devices": len(results),
            "reachable": sum(1 for r in results.values() if r["ssh_reachable"]),
            "unreachable": sum(1 for r in results.values() if not r["ssh_reachable"]),
        },
        "devices": results,
    }
    return json.dumps(report, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description="Validate SSH connectivity to network devices"
    )
    parser.add_argument(
        "--inventory",
        type=str,
        default="inventory",
        help="Path to Nornir inventory (default: inventory)",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Target specific device by name",
    )
    parser.add_argument(
        "--group",
        type=str,
        help="Target specific device group",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="SSH connection timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Number of connection retry attempts (default: 1)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        help="Write report to file (optional)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=None)
        
        if args.device:
            nr = nr.filter(name=args.device)
            logger.info(f"Filtered to device: {args.device}")
        elif args.group:
            nr = nr.filter(group_name=args.group)
            logger.info(f"Filtered to group: {args.group}")
        
        if not nr.inventory.hosts:
            logger.error("No devices matched specified criteria")
            return 1
        
        results = validate_devices(nr, timeout=args.timeout)
        
        if args.output == "json":
            output = format_json_report(results)
        else:
            output = format_text_report(results)
        
        print(output)
        
        if args.output_file:
            with open(args.output_file, "w") as f:
                f.write(output)
            logger.info(f"Report saved to {args.output_file}")
        
        accessible_count = sum(1 for r in results.values() if r["ssh_reachable"])
        return 0 if accessible_count == len(results) else 1
    
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```