```python
#!/usr/bin/env python
"""
Device Health Monitor - Nornir-based system health and availability reporting.

This script collects system health metrics from network devices (CPU, memory, disk,
uptime) and generates a health report. Useful for identifying devices approaching
capacity limits or experiencing unexpected reboots.

Usage:
    python device_health_monitor.py --hosts router1,router2,router3
    python device_health_monitor.py --group dc1 --cpu-threshold 80 --mem-threshold 75
    python device_health_monitor.py --hosts all --output report.csv

Prerequisites:
    - nornir with NAPALM plugin
    - Network devices with SNMP/SSH access
    - Device inventory configured in nornir config.yaml
"""

import argparse
import logging
import sys
from typing import Dict, List, Optional
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with appropriate verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=level
    )


def get_device_health(task: Task) -> Result:
    """Retrieve device health metrics using NAPALM get_facts."""
    try:
        result = task.run(
            napalm_get,
            getters=['facts']
        )
        facts = result[0].result
        return Result(host=task.host, result=facts)
    except Exception as e:
        logging.error(f"{task.host.name}: Failed to retrieve health data - {e}")
        return Result(host=task.host, result=None, failed=True)


def check_health_thresholds(
    facts: Dict,
    cpu_threshold: int,
    mem_threshold: int
) -> Dict:
    """Check device metrics against thresholds and return status."""
    status = {
        'host': facts.get('hostname', 'unknown'),
        'model': facts.get('model', 'unknown'),
        'uptime': facts.get('uptime_seconds', 0),
        'cpu': facts.get('cpu_utilization', 'N/A'),
        'memory': facts.get('memory_used_percent', 'N/A'),
        'warnings': []
    }

    if isinstance(status['cpu'], (int, float)):
        if status['cpu'] > cpu_threshold:
            status['warnings'].append(
                f"CPU {status['cpu']}% exceeds {cpu_threshold}% threshold"
            )

    if isinstance(status['memory'], (int, float)):
        if status['memory'] > mem_threshold:
            status['warnings'].append(
                f"Memory {status['memory']}% exceeds {mem_threshold}% threshold"
            )

    if status['uptime'] < 3600:
        hours = status['uptime'] / 3600
        status['warnings'].append(f"Recently rebooted ({hours:.1f} hours ago)")

    return status


def format_uptime(seconds: int) -> str:
    """Convert uptime seconds to human-readable format."""
    if not seconds:
        return "Unknown"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    return f"{days}d {hours}h"


def generate_report(
    results: List[Dict],
    output_file: Optional[str] = None
) -> None:
    """Generate and display health report."""
    print("\n" + "="*80)
    print("DEVICE HEALTH REPORT")
    print("="*80)

    healthy = 0
    warned = 0

    for status in results:
        if status is None:
            continue

        print(f"\nHost: {status['host']} ({status['model']})")
        print(f"  Uptime: {format_uptime(status['uptime'])}")
        print(f"  CPU: {status['cpu']}%")
        print(f"  Memory: {status['memory']}%")

        if status['warnings']:
            warned += 1
            print("  ⚠ Warnings:")
            for warning in status['warnings']:
                print(f"    - {warning}")
        else:
            healthy += 1
            print("  ✓ Healthy")

    print("\n" + "="*80)
    print(f"Summary: {healthy} healthy, {warned} with warnings")
    print("="*80 + "\n")

    if output_file:
        try:
            with open(output_file, 'w') as f:
                f.write("Hostname,Model,Uptime,CPU,Memory,Warnings\n")
                for status in results:
                    if status:
                        warnings_str = "; ".join(status['warnings']) or "None"
                        f.write(
                            f"{status['host']},{status['model']},"
                            f"{format_uptime(status['uptime'])},"
                            f"{status['cpu']}%,{status['memory']}%,"
                            f"\"{warnings_str}\"\n"
                        )
            logging.info(f"Report saved to {output_file}")
        except IOError as e:
            logging.error(f"Failed to write output file: {e}")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description='Monitor network device health metrics'
    )
    parser.add_argument(
        '--hosts',
        type=str,
        default='all',
        help='Comma-separated list of hosts or "all"'
    )
    parser.add_argument(
        '--group',
        type=str,
        help='Filter by device group'
    )
    parser.add_argument(
        '--cpu-threshold',
        type=int,
        default=80,
        help='CPU utilization warning threshold (%%, default: 80)'
    )
    parser.add_argument(
        '--mem-threshold',
        type=int,
        default=85,
        help='Memory usage warning threshold (%%, default: 85)'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Export results to CSV file'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        nr = InitNornir(config_file='config.yaml')

        if args.group:
            nr = nr.filter(group=args.group)
        elif args.hosts != 'all':
            hosts_list = [h.strip() for h in args.hosts.split(',')]
            nr = nr.filter(filter_func=lambda h: h.name in hosts_list)

        logging.info(f"Running health check on {len(nr.inventory.hosts)} devices")

        results = nr.run(task=get_device_health)

        health_statuses = []
        for host_name, multi_result in results.items():
            for task_result in multi_result.values():
                if task_result.result:
                    status = check_health_thresholds(
                        task_result.result,
                        args.cpu_threshold,
                        args.mem_threshold
                    )
                    health_statuses.append(status)
                else:
                    logging.warning(f"No data retrieved for {host_name}")

        generate_report(health_statuses, args.output)

    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == '__main__':
    main()
```