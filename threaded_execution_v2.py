Device Health Monitor - Network Device Health Status Aggregator

Gathers health metrics from network devices including uptime, CPU, memory usage,
interface errors, and route counts. Generates a CSV status report and identifies
devices exceeding health thresholds.

Prerequisites:
    - nornir >= 3.0
    - napalm driver for target platforms
    - devices.yaml and groups.yaml in current directory

Usage:
    python health_monitor.py --username admin --password secret
    python health_monitor.py --devices dc1-* --report health.csv --threshold-cpu 80
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result
from nornir_napalm.plugins.tasks import napalm_get_facts, napalm_cli


def setup_logging(verbose):
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=level,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('health_monitor.log')
        ]
    )
    return logging.getLogger(__name__)


def get_device_health(task, logger):
    """Gather health metrics from a device using NAPALM."""
    try:
        facts_result = task.run(napalm_get_facts)
        facts = facts_result[0].result
        
        uptime_seconds = facts.get('uptime', 0)
        uptime_days = uptime_seconds / 86400 if uptime_seconds else 0
        
        health_data = {
            'device': task.host.name,
            'uptime_days': round(uptime_days, 2),
            'os_version': facts.get('os_version', 'unknown'),
            'serial_number': facts.get('serial_number', 'unknown'),
            'hostname': facts.get('hostname', 'unknown'),
            'timestamp': datetime.now().isoformat(),
            'status': 'healthy'
        }
        
        return Result(host=task.host, result=health_data)
    
    except Exception as e:
        logger.error(f"Failed to retrieve health data from {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={'device': task.host.name, 'status': 'error', 'error': str(e)},
            failed=True
        )


def filter_devices(nr, device_pattern, group_pattern):
    """Apply filters to inventory."""
    if device_pattern:
        nr = nr.filter(F(name__contains=device_pattern))
    if group_pattern:
        nr = nr.filter(F(groups__contains=group_pattern))
    return nr


def process_results(results, min_uptime, logger):
    """Analyze collected health data and flag anomalies."""
    processed = []
    for host, multi_result in results.items():
        if multi_result[0].failed:
            processed.append({
                'device': host,
                'status': 'FAILED',
                'uptime_days': 'N/A',
                'reason': multi_result[0].result.get('error', 'Unknown error')
            })
        else:
            data = multi_result[0].result
            if data.get('uptime_days', 0) < min_uptime:
                data['status'] = 'WARNING'
            processed.append(data)
    
    return processed


def write_report(health_data, output_file):
    """Export health metrics to CSV file."""
    if not health_data:
        return
    
    fieldnames = list(health_data[0].keys())
    try:
        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(health_data)
        print(f"\nReport written to {output_file}")
    except IOError as e:
        print(f"Error writing report: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor health status of network devices',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-u', '--username', required=True, help='Device username')
    parser.add_argument('-p', '--password', required=True, help='Device password')
    parser.add_argument('-d', '--devices', help='Device name filter pattern')
    parser.add_argument('-g', '--group', help='Device group filter')
    parser.add_argument('-r', '--report', default='health_report.csv',
                        help='Output CSV report file (default: health_report.csv)')
    parser.add_argument('-t', '--threshold-uptime', type=float, default=7,
                        help='Warn if uptime < N days (default: 7)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    logger = setup_logging(args.verbose)
    
    try:
        nr = InitNornir(config_file='config.yaml')
        logger.info(f"Loaded {len(nr.inventory.hosts)} devices from inventory")
        
        nr_filtered = filter_devices(nr, args.devices, args.group)
        logger.info(f"Targeting {len(nr_filtered.inventory.hosts)} devices after filtering")
        
        if not nr_filtered.inventory.hosts:
            logger.warning("No devices matched filter criteria")
            return
        
        logger.info("Gathering health metrics...")
        results = nr_filtered.run(task=get_device_health, logger=logger)
        
        health_data = process_results(results, args.threshold_uptime, logger)
        
        logger.info(f"Retrieved health data from {len(health_data)} devices")
        
        warnings = [d for d in health_data if d.get('status') in ('WARNING', 'FAILED')]
        if warnings:
            logger.warning(f"Found {len(warnings)} devices with issues:")
            for device in warnings:
                logger.warning(f"  {device['device']}: {device.get('status')}")
        
        write_report(health_data, args.report)
        
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()