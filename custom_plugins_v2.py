```python
"""
Device Software Version Auditor

Purpose:
    Collects software versions from network devices, compares against
    recommended versions, and generates compliance audit report. Identifies
    outdated devices and highlights version inconsistencies across the network.

Usage:
    python 058_device_version_auditor.py \\
        --devices all \\
        --expected-versions device_versions.json \\
        --report version_audit.json

Prerequisites:
    - Nornir inventory properly configured (hosts.yaml, groups.yaml)
    - Device credentials with read access
    - Network connectivity to all devices
    - Optional JSON baseline file with expected/recommended versions
"""

import argparse
import json
import logging
import re
from datetime import datetime
from typing import Dict, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_version(output: str, device_type: str) -> Optional[str]:
    """Extract software version from device output based on device type."""
    if 'cisco' in device_type.lower() or 'ios' in device_type.lower():
        match = re.search(r'Cisco IOS.*?Version\s+([\d.]+)', output, re.IGNORECASE)
        if match:
            return match.group(1)
    elif 'juniper' in device_type.lower() or 'junos' in device_type.lower():
        match = re.search(r'Junos:\s+([\d.]+)', output)
        if match:
            return match.group(1)
    elif 'arista' in device_type.lower():
        match = re.search(r'Software version:\s+([\d.]+)', output)
        if match:
            return match.group(1)
    return None


def get_device_version(task: Task) -> Result:
    """Collect device version via netmiko."""
    try:
        output = task.run(
            netmiko_send_command,
            command_string="show version"
        )
        
        version = extract_version(output.result, task.host.device_type)
        
        return Result(
            host=task.host,
            result={
                'device': task.host.name,
                'device_type': task.host.device_type,
                'version': version
            }
        )
    except Exception as e:
        logger.error(f"{task.host.name}: {str(e)}")
        return Result(
            host=task.host,
            result={
                'device': task.host.name,
                'device_type': task.host.device_type,
                'version': None,
                'error': str(e)
            },
            failed=True
        )


def load_expectations(filepath: str) -> Dict:
    """Load expected versions from JSON file."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Expectations file not found: {filepath}")
        return {}


def main():
    parser = argparse.ArgumentParser(
        description='Audit network device software versions for compliance'
    )
    parser.add_argument(
        '--devices',
        default='all',
        help='Specific devices to audit (all, group name, or space-separated names)'
    )
    parser.add_argument(
        '--expected-versions',
        help='JSON file with expected versions per device type'
    )
    parser.add_argument(
        '--report',
        help='Save JSON audit report to file'
    )
    
    args = parser.parse_args()
    
    nr = InitNornir(config_file='config.yaml')
    
    if args.devices != 'all':
        if ',' in args.devices or ' ' in args.devices:
            device_list = re.split(r'[,\s]+', args.devices.strip())
            nr = nr.filter(name__in=device_list)
        else:
            nr = nr.filter(name=args.devices)
    
    logger.info(f"Collecting versions from {len(nr.inventory.hosts)} devices")
    results = nr.run(task=get_device_version, num_workers=4)
    
    expectations = {}
    if args.expected_versions:
        expectations = load_expectations(args.expected_versions)
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'total_devices': len(nr.inventory.hosts),
        'devices_audited': 0,
        'devices_outdated': 0,
        'devices_unknown': 0,
        'devices': {}
    }
    
    for device_name, task_result in results.items():
        if not task_result:
            continue
        
        result_data = task_result[0].result
        device_type = result_data.get('device_type', 'unknown')
        version = result_data.get('version')
        
        if 'error' not in result_data:
            report['devices_audited'] += 1
        
        expected_version = expectations.get(device_type, {}).get('recommended')
        is_outdated = False
        
        if version and expected_version:
            if version < expected_version:
                is_outdated = True
                report['devices_outdated'] += 1
        elif not version:
            report['devices_unknown'] += 1
        
        status = 'OUTDATED' if is_outdated else 'OK' if version else 'UNKNOWN'
        report['devices'][device_name] = {
            'type': device_type,
            'current_version': version,
            'expected_version': expected_version,
            'status': status
        }
    
    print("\n" + "="*70)
    print("DEVICE SOFTWARE VERSION AUDIT REPORT")
    print("="*70)
    print(f"Timestamp: {report['timestamp']}")
    print(f"Devices audited: {report['devices_audited']}/{report['total_devices']}")
    print(f"Outdated devices: {report['devices_outdated']}")
    print(f"Unknown versions: {report['devices_unknown']}")
    print("-"*70)
    print(f"{'Device':<20} {'Type':<15} {'Version':<15} {'Status':<12}")
    print("-"*70)
    
    for device_name in sorted(report['devices'].keys()):
        info = report['devices'][device_name]
        print(
            f"{device_name:<20} {info['type']:<15} "
            f"{str(info['current_version']):<15} {info['status']:<12}"
        )
    
    print("="*70 + "\n")
    
    if args.report:
        with open(args.report, 'w') as f:
            json.dump(report, f, indent=2)
        logger.info(f"Report saved to {args.report}")


if __name__ == '__main__':
    main()
```