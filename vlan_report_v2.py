```python
"""
DNS and NTP Synchronization Validator

Validates DNS resolution and NTP synchronization status across network devices,
identifying devices with time drift, DNS failures, or synchronization issues.

Purpose:
  - Verify DNS resolution on all devices
  - Check NTP peer status and synchronization
  - Detect time drift across the network
  - Identify devices with invalid NTP configuration
  - Generate compliance report

Usage:
  python dns_ntp_validator.py --inventory inventory/
  python dns_ntp_validator.py --device router1 router2 --output report.json
  python dns_ntp_validator.py --inventory inventory/ --ntp-threshold 100

Prerequisites:
  - Nornir with netmiko plugin
  - SSH access to devices
  - Devices must support 'show ntp status' and 'show ip dns'
"""

import argparse
import json
import logging
from datetime import datetime
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def setup_logging(verbose=False):
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def validate_dns(task: Task) -> Result:
    """Test DNS resolution on device."""
    dns_result = {'device': task.host.name, 'dns_working': False, 'servers': []}
    
    try:
        output = task.run(netmiko_send_command, command_string='show ip dns')
        dns_result['raw_output'] = output[0].result
        dns_result['dns_working'] = parse_dns_servers(output[0].result, dns_result)
    except Exception as e:
        logger.warning(f"{task.host.name}: DNS check failed - {e}")
        dns_result['error'] = str(e)
    
    return Result(host=task.host, result=dns_result)


def validate_ntp(task: Task, threshold_ms=100) -> Result:
    """Check NTP synchronization status."""
    ntp_result = {
        'device': task.host.name,
        'synchronized': False,
        'peers': [],
        'stratum': None,
        'offset_ms': None
    }
    
    try:
        output = task.run(netmiko_send_command, command_string='show ntp status')
        parse_ntp_status(output[0].result, ntp_result, threshold_ms)
    except Exception as e:
        logger.warning(f"{task.host.name}: NTP check failed - {e}")
        ntp_result['error'] = str(e)
    
    return Result(host=task.host, result=ntp_result)


def parse_dns_servers(output, dns_result):
    """Extract DNS servers from output."""
    try:
        for line in output.split('\n'):
            if 'server' in line.lower() or 'nameserver' in line.lower():
                parts = line.split()
                if len(parts) >= 2 and '.' in parts[-1]:
                    dns_result['servers'].append(parts[-1])
        return len(dns_result['servers']) > 0
    except:
        return False


def parse_ntp_status(output, ntp_result, threshold_ms):
    """Extract NTP status from output."""
    try:
        lines = output.split('\n')
        
        for line in lines:
            lower = line.lower()
            
            if 'synchronized' in lower or 'sync' in lower:
                ntp_result['synchronized'] = 'yes' in lower or 'true' in lower
            
            if 'stratum' in lower:
                parts = line.split(':')
                if len(parts) > 1:
                    ntp_result['stratum'] = parts[1].strip().split()[0]
            
            if 'offset' in lower:
                parts = line.split(':')
                if len(parts) > 1:
                    try:
                        offset = float(parts[1].split()[0].rstrip('ms'))
                        ntp_result['offset_ms'] = offset
                        if abs(offset) > threshold_ms:
                            ntp_result['time_drift'] = True
                    except:
                        pass
            
            if 'reference' in lower or 'peer' in lower:
                if len(line.split()) > 1:
                    ntp_result['peers'].append(line.strip())
    
    except Exception as e:
        logger.debug(f"NTP parsing error: {e}")


def check_device(task: Task, ntp_threshold=100):
    """Run all validation checks on device."""
    dns_check = task.run(validate_dns)
    ntp_check = task.run(validate_ntp, threshold_ms=ntp_threshold)
    
    return Result(
        host=task.host,
        result={
            'device': task.host.name,
            'dns': dns_check[0].result,
            'ntp': ntp_check[0].result
        }
    )


def generate_report(results, output_file=None):
    """Compile and output validation report."""
    report = {
        'timestamp': datetime.now().isoformat(),
        'total_devices': len(results),
        'dns_compliant': 0,
        'ntp_compliant': 0,
        'devices': []
    }
    
    for device_name, multi_result in results.items():
        if not multi_result:
            continue
        
        device_data = multi_result[0].result
        report['devices'].append(device_data)
        
        if device_data['dns']['dns_working']:
            report['dns_compliant'] += 1
        if device_data['ntp']['synchronized']:
            report['ntp_compliant'] += 1
    
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        logger.info(f"Report saved to {output_file}")
    else:
        print_report(report)
    
    return report


def print_report(report):
    """Print formatted validation report."""
    print("\n" + "="*80)
    print("DNS AND NTP SYNCHRONIZATION VALIDATION REPORT")
    print("="*80)
    print(f"Timestamp: {report['timestamp']}")
    print(f"Total Devices: {report['total_devices']}")
    print(f"DNS Compliant: {report['dns_compliant']}/{report['total_devices']}")
    print(f"NTP Synchronized: {report['ntp_compliant']}/{report['total_devices']}\n")
    
    for device in report['devices']:
        print(f"Device: {device['device']}")
        
        dns = device['dns']
        dns_status = "✓" if dns['dns_working'] else "✗"
        print(f"  DNS {dns_status}: {len(dns.get('servers', []))} servers configured")
        
        ntp = device['ntp']
        ntp_status = "✓" if ntp['synchronized'] else "✗"
        offset = ntp.get('offset_ms', 'N/A')
        print(f"  NTP {ntp_status}: Stratum {ntp.get('stratum', '?')}, Offset {offset}ms")
        print()


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description='Validate DNS and NTP on network devices'
    )
    parser.add_argument(
        '--inventory',
        default='inventory/',
        help='Nornir inventory path'
    )
    parser.add_argument(
        '--device',
        action='append',
        dest='devices',
        help='Target specific device(s)'
    )
    parser.add_argument(
        '--output',
        help='JSON output file path'
    )
    parser.add_argument(
        '--ntp-threshold',
        type=int,
        default=100,
        help='NTP offset threshold in milliseconds'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose logging'
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    try:
        logger.info("Initializing Nornir...")
        nr = InitNornir(config_file=f"{args.inventory}config.yaml")
        
        if args.devices:
            nr = nr.filter(name__in=args.devices)
        
        logger.info(f"Validating {len(nr.inventory.hosts)} devices...")
        results = nr.run(
            task=check_device,
            ntp_threshold=args.ntp_threshold
        )
        
        generate_report(results, args.output)
        logger.info("Validation complete")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        exit(1)


if __name__ == "__main__":
    main()
```