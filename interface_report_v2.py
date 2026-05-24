```python
"""
Device Facts Report - Gathers and reports device facts across the network.

Purpose:
    Collects device facts (model, OS version, serial number, uptime) from
    network devices and generates a formatted report. Useful for inventory
    management, compliance auditing, and device tracking.

Prerequisites:
    - nornir installed with netmiko/napalm plugins
    - hosts.yaml and groups.yaml inventory files
    - Network connectivity to target devices
    - Device credentials configured in inventory

Usage:
    python device_facts_report.py
    python device_facts_report.py --device router-01
    python device_facts_report.py --group production --output csv
    python device_facts_report.py --filter 'device_type=="eos"' --output json
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get
from nornir.plugins.functions.text import print_result

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_facts(task):
    """Gather device facts using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["facts"])
        return result
    except Exception as e:
        logger.error(f"Failed to gather facts from {task.host.name}: {e}")
        raise


def format_uptime(seconds):
    """Convert seconds to human-readable uptime."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def generate_csv_report(results):
    """Generate CSV-formatted report."""
    header = "Device,Vendor,Model,OS Version,Serial Number,Uptime,Hostname"
    print(header)
    
    for device_name in sorted(results.keys()):
        result = results[device_name][0].result
        if result and 'facts' in result:
            facts = result['facts']
            uptime = format_uptime(facts.get('uptime', 0))
            print(f"{device_name},{facts.get('vendor', 'N/A')},{facts.get('model', 'N/A')},"
                  f"{facts.get('os_version', 'N/A')},{facts.get('serial_number', 'N/A')},"
                  f"{uptime},{facts.get('hostname', 'N/A')}")


def generate_text_report(results):
    """Generate human-readable text report."""
    print("\n" + "=" * 80)
    print("DEVICE FACTS REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")
    
    for device_name in sorted(results.keys()):
        result = results[device_name][0].result
        if result and 'facts' in result:
            facts = result['facts']
            uptime = format_uptime(facts.get('uptime', 0))
            
            print(f"Device: {device_name}")
            print(f"  Hostname:      {facts.get('hostname', 'N/A')}")
            print(f"  Vendor:        {facts.get('vendor', 'N/A')}")
            print(f"  Model:         {facts.get('model', 'N/A')}")
            print(f"  OS Version:    {facts.get('os_version', 'N/A')}")
            print(f"  Serial Number: {facts.get('serial_number', 'N/A')}")
            print(f"  Uptime:        {uptime}")
            print(f"  Interfaces:    {facts.get('interface_count', 'N/A')}")
            print()


def generate_json_report(results):
    """Generate JSON-formatted report."""
    report = {}
    for device_name in results.keys():
        result = results[device_name][0].result
        if result and 'facts' in result:
            facts = result['facts']
            report[device_name] = {
                'hostname': facts.get('hostname', 'N/A'),
                'vendor': facts.get('vendor', 'N/A'),
                'model': facts.get('model', 'N/A'),
                'os_version': facts.get('os_version', 'N/A'),
                'serial_number': facts.get('serial_number', 'N/A'),
                'uptime_seconds': facts.get('uptime', 0),
                'interface_count': facts.get('interface_count', 0)
            }
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description='Gather and report device facts from network inventory'
    )
    parser.add_argument('--device', help='Target specific device by name')
    parser.add_argument('--group', help='Filter devices by group')
    parser.add_argument('--filter', help='Advanced filter expression (e.g., vendor=="cisco")')
    parser.add_argument('--output', choices=['text', 'csv', 'json'], default='text',
                        help='Output format (default: text)')
    parser.add_argument('--log-level', default='WARNING', 
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Logging level')
    args = parser.parse_args()
    
    logger.setLevel(getattr(logging, args.log_level))
    
    try:
        nr = InitNornir(config_file="config.yaml")
        logger.info(f"Loaded inventory with {len(nr.inventory.hosts)} hosts")
        
        # Apply filters
        if args.device:
            nr = nr.filter(name=args.device)
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        elif args.filter:
            try:
                nr = nr.filter(F(eval(args.filter)))
            except Exception as e:
                logger.error(f"Invalid filter expression: {e}")
                return 1
        
        if len(nr.inventory.hosts) == 0:
            logger.error("No devices matched the specified criteria")
            return 1
        
        logger.info(f"Gathering facts from {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=gather_facts)
        
        # Check for failures
        failed_count = sum(1 for r in results.values() if r.failed)
        if failed_count > 0:
            logger.warning(f"{failed_count} device(s) failed to report facts")
        
        # Generate report
        if args.output == 'csv':
            generate_csv_report(results)
        elif args.output == 'json':
            generate_json_report(results)
        else:
            generate_text_report(results)
        
        return 0
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```