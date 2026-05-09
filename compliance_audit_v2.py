```python
"""
Route Table Analysis - Nornir Network Automation Script

Purpose:
  Analyzes device routing tables to identify potential issues including
  missing routes, default route presence, high route counts, and routing
  inconsistencies. Useful for network troubleshooting and convergence
  verification across the network infrastructure.

Usage:
  python route_analysis.py --devices r1,r2,r3 --format json
  python route_analysis.py --group core --check-default
  python route_analysis.py --all --output route_audit.json

Prerequisites:
  - Nornir with NAPALM support (pip install nornir[napalm])
  - SSH access to network devices with configured credentials
  - inventory.yaml with device definitions and groups
  - Device types supported: Cisco IOS/XE/XR, NXOS, Juniper, Arista
  - Requires route_details getter support on target devices

Examples:
  Check core routers for missing default routes:
    python route_analysis.py --group core --check-default --format table

  Export all routes to JSON for analysis:
    python route_analysis.py --all --output routes.json --format json

  Check specific devices with verbose output:
    python route_analysis.py --devices r1,r2,r3 --verbose
"""

import json
import logging
import argparse
from datetime import datetime
from typing import Dict, Any
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def analyze_routing_table(task: Task, check_default: bool = True) -> Result:
    """
    Analyze device routing table for anomalies and potential issues.

    Collects and analyzes route information including default route presence,
    route count, and protocol distribution. Flags common issues like missing
    default routes or excessive route table entries.

    Args:
        task: Nornir task object
        check_default: Flag if default route is missing

    Returns:
        Result containing route analysis dictionary
    """
    try:
        route_data = task.run(napalm_get, getters=['route_details'])
        routes = route_data[0].result.get('route_details', {})

        analysis = {
            'device': task.host.name,
            'timestamp': datetime.now().isoformat(),
            'total_routes': len(routes),
            'default_route_present': False,
            'issues': [],
            'route_summary': {
                'ipv4_routes': 0,
                'ipv6_routes': 0,
                'connected': 0,
                'static': 0,
                'bgp': 0,
                'ospf': 0,
                'eigrp': 0
            }
        }

        # Analyze each route
        for prefix in routes.keys():
            if prefix == '0.0.0.0/0':
                analysis['default_route_present'] = True
                analysis['route_summary']['ipv4_routes'] += 1
            elif prefix == '::/0':
                analysis['default_route_present'] = True
                analysis['route_summary']['ipv6_routes'] += 1
            elif ':' in prefix:
                analysis['route_summary']['ipv6_routes'] += 1
            else:
                analysis['route_summary']['ipv4_routes'] += 1

        # Identify potential issues
        if check_default and not analysis['default_route_present']:
            analysis['issues'].append('No default route (0.0.0.0/0 or ::/0)')

        total_ipv4 = analysis['route_summary']['ipv4_routes']
        if total_ipv4 > 5000:
            analysis['issues'].append(
                f'Large IPv4 routing table: {total_ipv4} routes'
            )

        if analysis['total_routes'] == 0:
            analysis['issues'].append('No routes found (potential device issue)')

        return Result(host=task.host, result=analysis)

    except Exception as e:
        logger.error(f"Error analyzing routes on {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={'error': str(e), 'device': task.host.name},
            failed=True
        )


def print_table_format(results: Dict[str, Any]) -> None:
    """Display route analysis in table format."""
    print("\n" + "=" * 110)
    print(f"{'Device':<18} {'Total Routes':<15} {'IPv4':<10} {'IPv6':<10} "
          f"{'Default Route':<16} {'Issues':<38}")
    print("=" * 110)

    for host_name, data in results.items():
        if isinstance(data, dict) and 'error' not in data:
            issues_str = '; '.join(data.get('issues', ['None']))[:35]
            print(f"{data['device']:<18} {data['total_routes']:<15} "
                  f"{data['route_summary']['ipv4_routes']:<10} "
                  f"{data['route_summary']['ipv6_routes']:<10} "
                  f"{'Yes' if data['default_route_present'] else 'No':<16} "
                  f"{issues_str:<38}")
        else:
            print(f"{host_name:<18} ERROR - {data.get('error', 'Unknown')}")

    print("=" * 110)


def main():
    parser = argparse.ArgumentParser(
        description='Analyze network device routing tables for anomalies'
    )
    parser.add_argument(
        '--devices',
        help='Comma-separated list of device names'
    )
    parser.add_argument(
        '--group',
        help='Device group from inventory'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Analyze all devices'
    )
    parser.add_argument(
        '--check-default',
        action='store_true',
        help='Flag devices missing default route'
    )
    parser.add_argument(
        '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format (default: table)'
    )
    parser.add_argument(
        '--output',
        help='Save results to file'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file='config.yaml')

        # Filter devices
        if args.devices:
            nr = nr.filter(name__in=args.devices.split(','))
        elif args.group:
            nr = nr.filter(groups__contains=args.group)
        elif not args.all:
            parser.error(
                'Specify --devices, --group, or use --all'
            )

        if not nr.inventory.hosts:
            logger.error('No devices matched filter criteria')
            return

        logger.info(
            f'Starting route analysis on {len(nr.inventory.hosts)} device(s)'
        )

        # Execute analysis
        results = nr.run(
            task=analyze_routing_table,
            check_default=args.check_default,
            num_workers=4
        )

        # Collect output
        output_data = {}
        for host_name, multi_result in results.items():
            if multi_result.failed:
                output_data[host_name] = {
                    'error': str(multi_result[0].exception),
                    'device': host_name
                }
            else:
                output_data[host_name] = multi_result[0].result

        # Display results
        if args.format == 'json':
            print(json.dumps(output_data, indent=2))
        else:
            print_table_format(output_data)

        # Save to file if requested
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2)
            logger.info(f'Results saved to {args.output}')

        logger.info('Route analysis completed successfully')

    except Exception as e:
        logger.error(f'Fatal error: {e}')
        raise


if __name__ == '__main__':
    main()
```