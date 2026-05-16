```python
#!/usr/bin/env python3
"""
Device Uptime and System Information Reporter

Purpose:
    Collects device uptime, system information, and generates reports on
    device reliability and maintenance history. Useful for identifying
    unstable devices, tracking reboots, and planning maintenance windows.

Usage:
    python device_uptime_report.py --output report.json
    python device_uptime_report.py --devices router1,router2 --format table
    python device_uptime_report.py --filter-group access

Prerequisites:
    - Nornir installed with netmiko driver
    - Inventory configured with SSH credentials
    - Network devices supporting 'show version' command
    - SSH connectivity to all devices
"""

import argparse
import csv
import json
import logging
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.tasks.networking import netmiko_send_command


def setup_logging(level: str = "INFO") -> None:
    """Configure logging output."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def parse_device_list(device_str: str) -> List[str]:
    """Parse comma-separated device names."""
    return [d.strip() for d in device_str.split(",") if d.strip()]


def collect_uptime_info(nr, devices: List[str] = None) -> Dict:
    """Collect uptime information from devices."""
    results = {}
    logger = logging.getLogger(__name__)
    
    if devices:
        inventory = nr.filter(F(name__in=devices))
    else:
        inventory = nr
    
    for host in inventory.inventory.hosts.values():
        logger.info(f"Collecting uptime from {host.name}")
        results[host.name] = {"timestamp": datetime.now().isoformat()}
        
        try:
            task = host.run_task(netmiko_send_command, command_string="show version")
            
            if task.ok:
                output = task.result
                results[host.name]["status"] = "success"
                
                for line in output.split("\n"):
                    if "uptime" in line.lower():
                        results[host.name]["uptime"] = line.strip()
                        break
                else:
                    results[host.name]["uptime"] = "Unable to parse"
            else:
                results[host.name]["status"] = "failed"
                results[host.name]["error"] = "Command execution failed"
                
        except Exception as e:
            logger.error(f"Error from {host.name}: {e}")
            results[host.name]["status"] = "error"
            results[host.name]["error"] = str(e)
    
    return results


def format_table(results: Dict) -> str:
    """Format results as ASCII table."""
    lines = [
        "\n" + "=" * 90,
        "Device Uptime Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 90,
        f"{'Device':<25} {'Status':<12} {'Uptime Information':<50}",
        "-" * 90,
    ]
    
    for device, info in results.items():
        status = info.get("status", "unknown").upper()
        uptime = info.get("uptime", info.get("error", "N/A"))[:48]
        lines.append(f"{device:<25} {status:<12} {uptime:<50}")
    
    lines.append("=" * 90)
    return "\n".join(lines) + "\n"


def format_csv(results: Dict) -> str:
    """Format results as CSV."""
    output = StringIO()
    writer = csv.DictWriter(
        output, fieldnames=["device", "status", "timestamp", "uptime", "error"]
    )
    writer.writeheader()
    
    for device, info in results.items():
        writer.writerow({
            "device": device,
            "status": info.get("status", "unknown"),
            "timestamp": info.get("timestamp", ""),
            "uptime": info.get("uptime", ""),
            "error": info.get("error", ""),
        })
    
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description="Collect device uptime and system information",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    parser.add_argument(
        "--devices", help="Comma-separated device names (default: all)"
    )
    parser.add_argument(
        "--filter-group",
        help="Filter inventory by group name",
    )
    parser.add_argument(
        "--output", help="Output file (default: stdout)"
    )
    parser.add_argument(
        "--format",
        choices=["json", "table", "csv"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--inventory",
        default="nornir_inventory",
        help="Path to nornir inventory directory",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices")
        
        if args.filter_group:
            nr = nr.filter(F(groups__contains=args.filter_group))
            logger.info(f"Filtered to group '{args.filter_group}': {len(nr.inventory.hosts)} devices")
        
        devices = parse_device_list(args.devices) if args.devices else None
        results = collect_uptime_info(nr, devices)
        
        if args.format == "json":
            output = json.dumps(results, indent=2, default=str)
        elif args.format == "csv":
            output = format_csv(results)
        else:
            output = format_table(results)
        
        if args.output:
            Path(args.output).write_text(output)
            logger.info(f"Report written to {args.output}")
        else:
            print(output)
        
        successful = sum(1 for r in results.values() if r.get("status") == "success")
        logger.info(f"Successfully collected from {successful}/{len(results)} devices")
        
    except FileNotFoundError as e:
        logger.error(f"Inventory not found: {e}")
        raise
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
```