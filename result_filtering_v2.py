```python
"""
Routing Table Analyzer - Extract, filter, and analyze device routing tables.

Purpose:
    Connects to network devices and retrieves routing table information using
    NAPALM. Supports filtering by protocol, prefix, and AD/metric. Useful for
    understanding routing topology, identifying route coverage, and auditing
    static/dynamic routing configuration.

Usage:
    python routing_table_analyzer.py --hosts all
    python routing_table_analyzer.py --filter-protocol bgp --filter-prefix 10.0.0.0/8
    python routing_table_analyzer.py --hosts router1 --output json

Prerequisites:
    - Nornir config.yaml with inventory
    - NAPALM library and device drivers
    - SSH/API access to network devices
    - Devices must support route_to getter
"""

import argparse
import json
import logging
from typing import Dict, List, Any, Optional
from nornir import InitNornir
from nornir.core.filter import F
from nornir_napalm.tasks import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def filter_routes(routes_dict: Dict[str, Any],
                  protocol: Optional[str] = None,
                  prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Filter routing table entries by protocol and/or destination prefix.

    Args:
        routes_dict: Routing table from NAPALM get_route_to()
        protocol: Filter by routing protocol (bgp, ospf, static, etc.)
        prefix: Filter by destination prefix (substring match)

    Returns:
        List of route entries matching criteria
    """
    filtered = []

    for dest_prefix, route_list in routes_dict.items():
        if prefix and prefix not in dest_prefix:
            continue

        for route in route_list:
            if protocol and route['protocol'].lower() != protocol.lower():
                continue

            filtered.append({
                'prefix': dest_prefix,
                'protocol': route['protocol'],
                'distance': route['distance'],
                'metric': route['metric'],
                'next_hop': route.get('next_hop', 'local'),
                'interface': route.get('outgoing_interface', 'N/A'),
            })

    return filtered


def analyze_routes(task, protocol: Optional[str] = None,
                   prefix: Optional[str] = None):
    """Nornir task to retrieve and analyze device routing table."""
    try:
        result = task.run(napalm_get, getters=['route_to'])
        routes_data = result[0].result.get('route_to', {})

        if not routes_data:
            logger.warning(f"{task.host.name}: No routes found")
            return None

        filtered_routes = filter_routes(routes_data, protocol, prefix)

        return {
            'host': task.host.name,
            'total_routes': len(routes_data),
            'filtered_count': len(filtered_routes),
            'routes': filtered_routes
        }

    except Exception as e:
        logger.error(f"{task.host.name}: Failed to retrieve routes - {e}")
        return None


def print_report(results: Dict, output_format: str = 'text'):
    """Print routing analysis report in specified format."""
    if output_format == 'json':
        report = {}
        for host, data in results.items():
            report[host] = data
        print(json.dumps(report, indent=2, default=str))
        return

    for host_name, data in results.items():
        if data is None:
            print(f"\n{host_name}: FAILED")
            continue

        print(f"\n{'='*75}")
        print(f"Host: {host_name}")
        print(f"Total Routes: {data['total_routes']} | "
              f"Matched Filter: {data['filtered_count']}")
        print(f"{'='*75}")

        if data['routes']:
            for route in data['routes'][:30]:
                print(
                    f"  {route['prefix']:<22} {route['protocol']:<10} "
                    f"AD: {route['distance']:<3} Metric: {route['metric']:<10} "
                    f"via {route['next_hop']}"
                )
            if len(data['routes']) > 30:
                print(f"  ... and {len(data['routes']) - 30} more routes")
        else:
            print("  (No routes match filter criteria)")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze routing tables across network devices'
    )
    parser.add_argument('--hosts', default='all',
                        help='Target host(s) (name or "all")')
    parser.add_argument('--platform', help='Filter by platform (ios, nxos, eos)')
    parser.add_argument('--filter-protocol',
                        help='Filter by protocol (bgp, ospf, static, connected)')
    parser.add_argument('--filter-prefix',
                        help='Filter by destination prefix (substring)')
    parser.add_argument('--output', choices=['text', 'json'], default='text',
                        help='Output format')

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file='config.yaml')

        if args.hosts != 'all':
            nr = nr.filter(F(name=args.hosts))

        if args.platform:
            nr = nr.filter(F(platform=args.platform))

        if not nr.inventory.hosts:
            logger.error('No hosts matched filter criteria')
            return 1

        logger.info(f'Retrieving routing tables from {len(nr.inventory.hosts)} '
                    f'device(s)')

        results = nr.run(
            task=analyze_routes,
            protocol=args.filter_protocol,
            prefix=args.filter_prefix,
            num_workers=4
        )

        output_data = {}
        for host_name in results:
            if results[host_name]:
                output_data[host_name] = results[host_name][0].result
            else:
                output_data[host_name] = None

        print_report(output_data, args.output)
        logger.info('Routing table analysis complete')

    except Exception as e:
        logger.error(f'Fatal error: {e}')
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
```