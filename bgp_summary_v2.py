```python
"""
Device Connectivity and Credential Auditor

Tests SSH connectivity and credential validity across network devices in inventory.
Useful for pre-change validation, credential rotation verification, and device
reachability audits.

Usage:
    python device_auditor.py --group core --username admin
    python device_auditor.py --hosts router1,router2,switch1 --password secret123
    python device_auditor.py --all --output audit_report.txt

Prerequisites:
    - Nornir installed with netmiko
    - Device inventory configured in config.yaml
    - SSH access to target devices
    - Credentials in inventory or provided via --username/--password
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_device_connectivity(task: Task) -> Result:
    """Test SSH connectivity and execute simple command to validate credentials."""
    try:
        result = task.run(
            netmiko_send_command,
            command_string='show version',
            use_textfsm=False
        )
        return Result(
            host=task.host,
            result={
                'status': 'reachable',
                'platform': task.host.platform,
                'command_output_lines': len(str(result[0].result).split('\n'))
            }
        )
    except Exception as e:
        return Result(
            host=task.host,
            result={
                'status': 'unreachable',
                'platform': task.host.platform,
                'error': str(e)
            },
            failed=True
        )


def generate_audit_report(audit_results: Dict) -> str:
    """Generate formatted audit report from results."""
    reachable = [h for h, r in audit_results.items() if r['status'] == 'reachable']
    unreachable = [h for h, r in audit_results.items() if r['status'] == 'unreachable']
    
    report_lines = [
        f"\n{'=' * 70}",
        f"Device Connectivity and Credential Audit Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"{'=' * 70}",
        f"\nSummary:",
        f"  Total devices tested: {len(audit_results)}",
        f"  Reachable: {len(reachable)} ({100*len(reachable)//len(audit_results) if audit_results else 0}%)",
        f"  Unreachable: {len(unreachable)}",
    ]
    
    if reachable:
        report_lines.extend([
            f"\n{'Reachable Devices:':-<50}",
        ])
        for host in sorted(reachable):
            platform = audit_results[host].get('platform', 'unknown')
            report_lines.append(f"  ✓ {host:<30} ({platform})")
    
    if unreachable:
        report_lines.extend([
            f"\n{'Unreachable Devices:':-<50}",
        ])
        for host in sorted(unreachable):
            error = audit_results[host].get('error', 'unknown error')
            report_lines.append(f"  ✗ {host:<30} Error: {error[:40]}")
    
    report_lines.append(f"{'=' * 70}\n")
    
    return '\n'.join(report_lines)


def run_audit(nr, hosts: List[str] = None, group: str = None) -> Dict:
    """Execute connectivity audit across specified devices."""
    if hosts:
        host_list = [h.strip() for h in hosts]
        filtered = nr.filter(F(name__in=host_list))
        scope_msg = f"hosts: {', '.join(host_list)}"
    elif group:
        filtered = nr.filter(F(groups__contains=group))
        scope_msg = f"group: {group}"
    else:
        filtered = nr
        scope_msg = "all inventory"
    
    if not filtered.inventory.hosts:
        logger.error(f"No devices found matching {scope_msg}")
        return {}
    
    logger.info(f"Starting audit of {scope_msg} ({len(filtered.inventory.hosts)} device(s))")
    
    results = filtered.run(task=test_device_connectivity, num_workers=4)
    
    audit_results = {}
    for host_name, task_results in results.items():
        for task_result in task_results.values():
            audit_results[host_name] = task_result.result
            status_symbol = "✓" if task_result.result['status'] == 'reachable' else "✗"
            logger.info(
                f"{status_symbol} {host_name}: {task_result.result['status']}"
            )
    
    return audit_results


def main():
    parser = argparse.ArgumentParser(
        description='Audit device connectivity and credential validity'
    )
    parser.add_argument(
        '--hosts',
        help='Comma-separated list of device hostnames'
    )
    parser.add_argument(
        '--group',
        help='Nornir inventory group to audit'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Audit all devices in inventory'
    )
    parser.add_argument(
        '--username',
        help='SSH username (overrides inventory setting)'
    )
    parser.add_argument(
        '--password',
        help='SSH password (overrides inventory setting)'
    )
    parser.add_argument(
        '--output',
        help='Output file for report (stdout if not specified)'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging verbosity'
    )
    
    args = parser.parse_args()
    logger.setLevel(args.log_level)
    
    if not any([args.hosts, args.group, args.all]):
        logger.error("Specify --hosts, --group, or --all")
        sys.exit(1)
    
    try:
        nr = InitNornir(config_file='config.yaml')
    except FileNotFoundError:
        logger.error("config.yaml not found in current directory")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        sys.exit(1)
    
    if args.username:
        for host in nr.inventory.hosts.values():
            host.username = args.username
    if args.password:
        for host in nr.inventory.hosts.values():
            host.password = args.password
    
    audit_results = run_audit(
        nr,
        hosts=[h.strip() for h in args.hosts.split(',')] if args.hosts else None,
        group=args.group
    )
    
    if not audit_results:
        logger.error("Audit produced no results")
        sys.exit(1)
    
    report = generate_audit_report(audit_results)
    
    if args.output:
        Path(args.output).write_text(report)
        logger.info(f"Report saved to {args.output}")
    else:
        print(report)


if __name__ == '__main__':
    main()
```