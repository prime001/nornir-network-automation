```python
#!/usr/bin/env python3
"""
Device Reachability and Basic Facts Collector

Purpose:
  Validates device reachability and collects basic facts (hostname, model,
  version, serial number) from network devices. Useful for asset inventory,
  device health monitoring, and network discovery tasks.

Usage:
  python3 010_device_facts_collector.py --target-group core --username admin
  python3 010_device_facts_collector.py --device router1 --username admin --password secret

Prerequisites:
  - Nornir installed with netmiko transport
  - Devices support 'show version' command (Cisco IOS/IOS-XE preferred)
  - Inventory configured (hosts.yaml, groups.yaml, defaults.yaml)
  - SSH access to devices

Example:
  $ python3 010_device_facts_collector.py --target-group access --username netadmin
  Device: switch1
    Status: REACHABLE
    Model: Cisco Catalyst 2960X
    Version: 15.2(4)E6
    Serial: ABC123456789
  
  Device: switch2
    Status: UNREACHABLE
    Error: Connection timeout
"""

import argparse
import logging
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import netmiko_send_command


def setup_logging(verbose: bool) -> logging.Logger:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


def extract_version_info(output: str, platform: str) -> dict:
    """Extract device info from show version output."""
    info = {"model": "Unknown", "version": "Unknown", "serial": "Unknown"}
    
    lines = output.split("\n")
    for line in lines:
        if "Model Number" in line or "Chassis Type" in line:
            info["model"] = line.split()[-1].rstrip(",")
        elif "Software Version" in line or ("Version" in line and "IOS" in line):
            parts = line.split()
            for i, part in enumerate(parts):
                if "Version" in part and i + 1 < len(parts):
                    info["version"] = parts[i + 1].rstrip(",")
        elif "Processor board ID" in line or "System Serial Number" in line:
            info["serial"] = line.split()[-1].rstrip(",")
    
    return info


def collect_device_facts(task, **kwargs):
    """Gather device facts via netmiko."""
    device_name = task.host.name
    platform = task.host.platform or "unknown"
    
    result = {
        "device": device_name,
        "status": "UNKNOWN",
        "model": "N/A",
        "version": "N/A",
        "serial": "N/A",
        "error": None,
    }
    
    try:
        cmd_result = task.run(
            netmiko_send_command,
            command_string="show version",
        )
        
        if cmd_result[0].failed:
            result["status"] = "FAILED"
            result["error"] = "Command execution failed"
        else:
            version_output = cmd_result[0].result
            info = extract_version_info(version_output, platform)
            result.update({
                "status": "REACHABLE",
                "model": info["model"],
                "version": info["version"],
                "serial": info["serial"],
            })
    except Exception as e:
        result["status"] = "UNREACHABLE"
        result["error"] = str(e)
    
    return result


def print_results(results: dict) -> None:
    """Print formatted results."""
    print("\n" + "=" * 70)
    print("DEVICE FACTS COLLECTION REPORT")
    print("=" * 70)
    
    reachable = 0
    unreachable = 0
    
    for hostname, task_result in results.items():
        if task_result.result is None:
            continue
        
        data = task_result.result
        status_ok = data["status"] == "REACHABLE"
        symbol = "✓" if status_ok else "✗"
        
        print(f"\n{symbol} Device: {data['device']}")
        print(f"  Status: {data['status']}")
        print(f"  Model: {data['model']}")
        print(f"  Version: {data['version']}")
        print(f"  Serial: {data['serial']}")
        
        if data["error"]:
            print(f"  Error: {data['error']}")
        
        if status_ok:
            reachable += 1
        else:
            unreachable += 1
    
    print("\n" + "=" * 70)
    print(f"Summary: {reachable} reachable, {unreachable} unreachable")
    print("=" * 70 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Collect device facts and validate reachability"
    )
    parser.add_argument(
        "--target-group",
        type=str,
        help="Target group name from inventory",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Single device hostname to check",
    )
    parser.add_argument(
        "--username",
        type=str,
        required=True,
        help="Network device username",
    )
    parser.add_argument(
        "--password",
        type=str,
        help="Network device password",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Connection timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    logger = setup_logging(args.verbose)
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.device:
            nr = nr.filter(name=args.device)
        elif args.target_group:
            nr = nr.filter(F(groups__contains=args.target_group))
        else:
            logger.error("Must specify --device or --target-group")
            return 1
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter criteria")
            return 1
        
        for host in nr.inventory.hosts.values():
            host.username = args.username
            if args.password:
                host.password = args.password
            host.conn_timeout = args.timeout
        
        logger.info(f"Collecting facts from {len(nr.inventory.hosts)} device(s)")
        
        results = nr.run(task=collect_device_facts)
        
        print_results(results)
        
        return 0
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```