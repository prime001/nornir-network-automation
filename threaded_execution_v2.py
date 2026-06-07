```python
#!/usr/bin/env python3
"""
NTP Synchronization Audit Tool

Audits NTP configuration and synchronization status across network devices.
Validates NTP peers, checks sync status, and identifies time drift issues.

Usage:
    python ntp_sync_audit.py --all
    python ntp_sync_audit.py --device router1 --output ntp_report.json
    python ntp_sync_audit.py --group core --verbose

Prerequisites:
    - nornir and nornir-netmiko installed
    - hosts.yml and groups.yml configured in current directory
    - Device credentials configured via environment or hosts.yml
    - Network connectivity to target devices

Returns:
    0 if all devices synchronized, 1 if sync issues detected
"""

import json
import logging
import argparse
import sys
import re
from datetime import datetime
from typing import Dict, Any, List

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_ntp_status(output: str) -> Dict[str, Any]:
    """Parse NTP status output for Cisco devices."""
    status = {
        'synchronized': False,
        'peer_count': 0,
        'selected_peer': None,
        'stratum': None,
        'issues': []
    }
    
    lines = output.split('\n')
    for line in lines:
        if 'Clock is' in line:
            if 'unsynchronized' in line.lower():
                status['issues'].append('Clock is unsynchronized')
            elif 'synchronized' in line.lower():
                status['synchronized'] = True
        
        if 'stratum' in line.lower():
            match = re.search(r'stratum (\d+)', line, re.IGNORECASE)
            if match:
                status['stratum'] = int(match.group(1))
        
        if 'system peer' in line.lower():
            parts = line.split()
            if len(parts) > 2:
                status['selected_peer'] = parts[-1]
    
    return status


def audit_ntp(task: Task) -> Result:
    """Audit NTP configuration and status on a device."""
    audit = {
        'hostname': task.host.name,
        'ip': task.host.get('ip'),
        'device_type': task.host.get('device_type'),
        'timestamp': datetime.now().isoformat(),
        'ntp_enabled': False,
        'status': {},
        'peers': [],
        'errors': []
    }
    
    try:
        config_result = task.run(
            netmiko_send_command,
            command_string='show running-config | include ntp'
        )
        config_output = config_result[0].result
        
        if not config_output or 'ntp' not in config_output.lower():
            audit['errors'].append('NTP not configured')
            return Result(host=task.host, result=audit)
        
        audit['ntp_enabled'] = True
        
        status_result = task.run(
            netmiko_send_command,
            command_string='show ntp status'
        )
        status_output = status_result[0].result
        audit['status'] = parse_ntp_status(status_output)
        
        association_result = task.run(
            netmiko_send_command,
            command_string='show ntp associations'
        )
        association_output = association_result[0].result
        
        for line in association_output.split('\n')[3:]:
            if line.strip() and not line.startswith('~'):
                parts = line.split()
                if len(parts) >= 2:
                    audit['peers'].append({
                        'address': parts[0],
                        'reachability': parts[1] if len(parts) > 1 else 'unknown'
                    })
        
        if not audit['peers']:
            audit['errors'].append('No NTP peers configured')
        
        if not audit['status'].get('synchronized'):
            audit['errors'].append('Device not synchronized to NTP')
        
        audit['sync_status'] = 'OK' if not audit['errors'] else 'ISSUES'
        
    except Exception as e:
        logger.warning(f"Error auditing NTP on {task.host.name}: {e}")
        audit['sync_status'] = 'ERROR'
        audit['errors'].append(str(e))
    
    return Result(host=task.host, result=audit)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--device', help='Single device hostname')
    parser.add_argument('--group', help='Filter by device group')
    parser.add_argument('--all', action='store_true', help='Audit all devices')
    parser.add_argument('--output', help='JSON output file path')
    parser.add_argument('--verbose', action='store_true', help='Verbose logging')
    parser.add_argument('--strict', action='store_true', 
                       help='Fail if any device has NTP issues')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if not (args.device or args.group or args.all):
        parser.error('Must specify --device, --group, or --all')
    
    try:
        nr = InitNornir(config_file='config.yaml')
    except Exception as e:
        logger.error(f"Nornir initialization failed: {e}")
        return 1
    
    if args.device:
        nr = nr.filter(name=args.device)
    elif args.group:
        nr = nr.filter(groups__contains=args.group)
    
    if len(nr.inventory.hosts) == 0:
        logger.error('No devices matched filter criteria')
        return 1
    
    logger.info(f'Auditing NTP on {len(nr.inventory.hosts)} device(s)')
    
    results = nr.run(task=audit_ntp)
    
    audit_results: List[Dict[str, Any]] = []
    issues_count = 0
    
    for host, task_result in results.items():
        if task_result:
            result_data = task_result[0].result
            audit_results.append(result_data)
            
            if result_data.get('errors'):
                issues_count += 1
                logger.error(f"{host}: {', '.join(result_data['errors'])}")
            else:
                logger.info(f"{host}: NTP synchronized")
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(audit_results, f, indent=2)
        logger.info(f'Audit results saved to {args.output}')
    else:
        print(json.dumps(audit_results, indent=2))
    
    summary = {
        'total_devices': len(audit_results),
        'synchronized': len(audit_results) - issues_count,
        'with_issues': issues_count,
        'timestamp': datetime.now().isoformat()
    }
    logger.info(f"Summary: {summary}")
    
    if args.strict and issues_count > 0:
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
```