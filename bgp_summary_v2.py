```python
"""
DNS and NTP Configuration Verifier

Purpose:
    Verify DNS and NTP configurations across network devices to ensure
    proper time synchronization and name resolution services are configured.
    Useful for infrastructure compliance and operational health checks.

Usage:
    python dns_ntp_verifier.py --devices router1 router2
    python dns_ntp_verifier.py --inventory custom_inventory.yaml

Prerequisites:
    - Nornir installed and configured with inventory
    - Netmiko library for device connectivity
    - SSH/Telnet access to target devices
    - Appropriate device credentials in inventory or .netrc

"""

import argparse
import logging
from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def verify_dns_ntp_config(task) -> dict:
    """Verify DNS and NTP configuration on target device."""
    hostname = task.host.name
    device_type = task.host.get('device_type', 'unknown')
    
    config = {
        'hostname': hostname,
        'device_type': device_type,
        'dns_servers': [],
        'ntp_servers': [],
        'ntp_status': '',
        'errors': []
    }
    
    try:
        if 'ios' in device_type.lower() or 'iosxe' in device_type.lower():
            dns_result = task.run(
                netmiko_send_command,
                command_string='show run | include ip name-server'
            )
            config['dns_servers'] = [
                line.strip() for line in dns_result[0].result.split('\n')
                if 'ip name-server' in line and line.strip()
            ]
            
            ntp_result = task.run(
                netmiko_send_command,
                command_string='show run | include ntp server'
            )
            config['ntp_servers'] = [
                line.strip() for line in ntp_result[0].result.split('\n')
                if 'ntp server' in line and line.strip()
            ]
            
            status_result = task.run(
                netmiko_send_command,
                command_string='show ntp status'
            )
            config['ntp_status'] = status_result[0].result[:200]
        
        elif 'junos' in device_type.lower():
            dns_result = task.run(
                netmiko_send_command,
                command_string='show configuration system name-server'
            )
            config['dns_servers'] = [
                line.strip() for line in dns_result[0].result.split('\n')
                if line.strip()
            ]
            
            ntp_result = task.run(
                netmiko_send_command,
                command_string='show configuration system ntp'
            )
            config['ntp_servers'] = [
                line.strip() for line in ntp_result[0].result.split('\n')
                if 'server' in line and line.strip()
            ]
            
            status_result = task.run(
                netmiko_send_command,
                command_string='show system ntp status'
            )
            config['ntp_status'] = status_result[0].result[:200]
        
        if not config['dns_servers']:
            config['errors'].append('No DNS servers configured')
        
        if not config['ntp_servers']:
            config['errors'].append('No NTP servers configured')
    
    except Exception as e:
        logger.warning(f"Configuration check failed on {hostname}: {e}")
        config['errors'].append(f'Check failed: {str(e)}')
    
    return config


def print_report(results: dict) -> None:
    """Display DNS and NTP configuration report."""
    print("\n" + "="*80)
    print("DNS AND NTP CONFIGURATION VERIFICATION REPORT")
    print("="*80)
    
    total_issues = 0
    
    for host, task_result in results.items():
        config = task_result[0].result
        issues = len(config['errors'])
        total_issues += issues
        
        print(f"\nDevice: {config['hostname']} ({config['device_type']})")
        print("-" * 80)
        
        print(f"DNS Servers ({len(config['dns_servers'])}):")
        if config['dns_servers']:
            for dns in config['dns_servers']:
                print(f"  {dns}")
        else:
            print(f"  (none configured)")
        
        print(f"\nNTP Servers ({len(config['ntp_servers'])}):")
        if config['ntp_servers']:
            for ntp in config['ntp_servers']:
                print(f"  {ntp}")
        else:
            print(f"  (none configured)")
        
        if config['errors']:
            print(f"\nIssues ({len(config['errors'])}):")
            for error in config['errors']:
                print(f"  ⚠️  {error}")
        else:
            print(f"\n✓ Configuration verified")
    
    print("\n" + "="*80)
    print(f"Total Issues Found: {total_issues}")
    print("="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Verify DNS and NTP configurations on network devices'
    )
    parser.add_argument(
        '--devices',
        nargs='+',
        help='Specific devices to verify (default: all in inventory)'
    )
    parser.add_argument(
        '--inventory',
        default='inventory.yaml',
        help='Path to Nornir inventory file (default: inventory.yaml)'
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.devices:
            nr = nr.filter(F(name__in=args.devices))
        
        if not nr.inventory.hosts:
            logger.error("No devices found in inventory")
            return 1
        
        logger.info(f"Verifying DNS/NTP configuration on {len(nr.inventory.hosts)} device(s)")
        
        results = nr.run(
            task=verify_dns_ntp_config,
            name="DNS/NTP Configuration Verification"
        )
        
        print_report(results)
        return 0
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
```