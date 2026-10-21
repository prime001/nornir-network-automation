```python
"""
Interface Description Audit Tool

Purpose:
    Audits network device interfaces to identify active ports missing
    descriptions. Helps maintain network documentation standards and
    identifies undocumented connectivity.

Usage:
    python interface_description_audit.py --inventory inventory.yaml \
                                          --format json --output report.json

Prerequisites:
    - nornir and nornir-netmiko installed
    - nornir inventory file configured
    - Network device SSH/netmiko connectivity
    - Devices support 'show interface' or equivalent

Examples:
    Basic audit with table output:
        python interface_description_audit.py

    Export to JSON:
        python interface_description_audit.py --format json --output audit.json

    Verbose mode with CSV:
        python interface_description_audit.py -v --format csv --output audit.csv
"""

import logging
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command


log = logging.getLogger(__name__)


def setup_logging(verbose: bool) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def audit_interfaces(task: Task) -> Result:
    """Retrieve interface status and description information."""
    device_type = task.host.get('platform', 'cisco_ios')
    
    cmd_map = {
        'cisco_ios': 'show interface',
        'cisco_iosxe': 'show interface',
        'cisco_nxos': 'show interface',
        'arista_eos': 'show interfaces',
    }
    
    cmd = cmd_map.get(device_type, 'show interface')
    
    try:
        result = task.run(netmiko_send_command, command_string=cmd)
        raw_output = result[0].result if result else ""
        interfaces = parse_interface_output(raw_output)
        return Result(host=task.host, result=interfaces)
    except Exception as e:
        log.warning(f"{task.host.name}: Failed to retrieve interfaces: {e}")
        return Result(host=task.host, result=[], failed=True)


def parse_interface_output(output: str) -> List[Dict[str, Any]]:
    """Parse interface output to extract name, status, and description."""
    interfaces = []
    current_iface = None
    
    for line in output.split('\n'):
        if_match = None
        for prefix in ('GigabitEthernet', 'FastEthernet', 'Ethernet',
                      'port-channel', 'Loopback', 'Vlan'):
            if line.startswith(prefix):
                if_match = line.split()[0]
                break
        
        if if_match:
            if current_iface and current_iface.get('status') == 'up':
                interfaces.append(current_iface)
            current_iface = {'name': if_match, 'status': 'unknown', 'description': ''}
        
        elif current_iface:
            if 'Description:' in line:
                desc = line.split(':', 1)[1].strip()
                current_iface['description'] = desc
            elif 'line protocol is' in line.lower():
                current_iface['status'] = 'up' if 'up' in line else 'down'
    
    if current_iface and current_iface.get('status') == 'up':
        interfaces.append(current_iface)
    
    return interfaces


def run_audit(nr) -> Dict[str, Any]:
    """Execute audit across all devices."""
    results = nr.run(task=audit_interfaces)
    
    report = {}
    for host, multi_result in results.items():
        if multi_result[0].failed:
            report[host] = {
                'status': 'error',
                'undocumented': [],
                'total': 0
            }
        else:
            interfaces = multi_result[0].result
            undocumented = [i['name'] for i in interfaces if not i['description']]
            report[host] = {
                'status': 'success',
                'total': len(interfaces),
                'undocumented_count': len(undocumented),
                'undocumented': undocumented
            }
    
    return report


def format_output(report: Dict[str, Any], fmt: str) -> str:
    """Format audit results."""
    if fmt == 'json':
        return json.dumps(report, indent=2)
    
    elif fmt == 'csv':
        lines = ['hostname,total_interfaces,undocumented_count,undocumented_list']
        for host, data in report.items():
            undoc_str = ';'.join(data['undocumented'])
            lines.append(f'{host},{data["total"]},{data["undocumented_count"]},{undoc_str}')
        return '\n'.join(lines)
    
    else:  # table format
        lines = ['Hostname | Active Interfaces | Undocumented | List']
        lines.append('-' * 80)
        for host, data in report.items():
            total = data['total']
            undoc_cnt = data['undocumented_count']
            undoc_list = ','.join(data['undocumented'][:3])
            if len(data['undocumented']) > 3:
                undoc_list += ',...'
            lines.append(f'{host:20} | {total:17} | {undoc_cnt:12} | {undoc_list}')
        return '\n'.join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Audit interface descriptions on network devices'
    )
    parser.add_argument(
        '--inventory',
        default='inventory.yaml',
        help='Path to nornir inventory file'
    )
    parser.add_argument(
        '--format',
        choices=['table', 'json', 'csv'],
        default='table',
        help='Output format'
    )
    parser.add_argument(
        '--output',
        help='Output file (default: stdout)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    inv_path = Path(args.inventory)
    if not inv_path.exists():
        log.error(f"Inventory file not found: {args.inventory}")
        sys.exit(1)
    
    try:
        nr = InitNornir(config_file=str(inv_path))
        log.info(f"Loaded inventory with {len(nr.inventory.hosts)} devices")
        
        report = run_audit(nr)
        output = format_output(report, args.format)
        
        if args.output:
            Path(args.output).write_text(output)
            log.info(f"Report saved to {args.output}")
        else:
            print(output)
        
        total_undoc = sum(d['undocumented_count'] for d in report.values())
        log.info(f"Audit complete: {total_undoc} undocumented interfaces found")
    
    except Exception as e:
        log.exception(f"Audit failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
```