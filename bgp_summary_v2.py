```python
"""Route Table Analyzer and Verification Tool.

Analyzes routing tables from network devices to identify inefficiencies,
overlaps, and potential issues. Generates recommendations for optimization.

Usage:
    python route_analyzer.py --devices all --analyze
    python route_analyzer.py --devices rtr1,rtr2 --export routes.json
    python route_analyzer.py --devices all --check-coverage 10.0.0.0/8

Prerequisites:
    - Nornir with inventory configured
    - NAPALM installed: pip install napalm
    - Devices must support get_route_to() via NAPALM
    - SSH/CLI access with proper credentials

Examples:
    Analyze all routing tables:
        python route_analyzer.py --devices all --analyze

    Check specific prefix coverage:
        python route_analyzer.py --devices all --check-coverage 172.16.0.0/12

    Export route data to JSON:
        python route_analyzer.py --devices rtr1,rtr2 --export routes.json
"""

import argparse
import logging
import json
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict
import ipaddress

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get

logger = logging.getLogger(__name__)


def get_routing_table(task: Task) -> Result:
    """Retrieve routing table using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["route_info"])
        routes = result[0].result.get("route_info", {})
        return Result(host=task.host, result=routes)
    except Exception as e:
        logger.error(f"Failed to get routes from {task.host.name}: {e}")
        return Result(host=task.host, result={}, failed=True)


def analyze_routes(routes: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze routing table for issues and statistics."""
    analysis = {
        "total_routes": len(routes),
        "default_routes": 0,
        "static_routes": 0,
        "dynamic_routes": 0,
        "issues": [],
        "protocol_distribution": defaultdict(int),
    }
    
    for prefix, route_data in routes.items():
        if prefix == "0.0.0.0/0" or prefix == "::/0":
            analysis["default_routes"] += 1
        
        if isinstance(route_data, list):
            for route in route_data:
                protocol = route.get("protocol", "unknown").lower()
                if "static" in protocol or protocol == "s":
                    analysis["static_routes"] += 1
                else:
                    analysis["dynamic_routes"] += 1
                
                analysis["protocol_distribution"][protocol] += 1
                
                metric = route.get("metric", 0)
                if isinstance(metric, (int, float)) and metric > 100:
                    analysis["issues"].append(
                        f"High metric: {prefix} via {route.get('via', 'N/A')} "
                        f"(metric: {metric})"
                    )
    
    return analysis


def check_coverage(routes: Dict[str, Any], target_prefix: str) -> Dict[str, Any]:
    """Check if a prefix is covered in the routing table."""
    try:
        target_net = ipaddress.ip_network(target_prefix, strict=False)
    except ValueError as e:
        return {"error": f"Invalid prefix: {e}", "covered": False}
    
    covering_routes = []
    for prefix in routes.keys():
        try:
            route_net = ipaddress.ip_network(prefix, strict=False)
            if target_net.subnet_of(route_net) or target_net == route_net:
                covering_routes.append(prefix)
        except ValueError:
            continue
    
    return {
        "target_prefix": target_prefix,
        "covered": len(covering_routes) > 0,
        "covering_routes": covering_routes,
    }


def print_analysis_report(results: Dict[str, Any]) -> None:
    """Print formatted analysis report."""
    print("\n" + "=" * 80)
    print(f"{'Route Analysis Report':<40}")
    print("=" * 80)
    
    for device_name in sorted(results.keys()):
        analysis = results[device_name]
        if isinstance(analysis, dict) and "error" not in analysis:
            print(f"\n{device_name}:")
            print(f"  Total Routes:        {analysis['total_routes']}")
            print(f"  Default Routes:      {analysis['default_routes']}")
            print(f"  Static Routes:       {analysis['static_routes']}")
            print(f"  Dynamic Routes:      {analysis['dynamic_routes']}")
            
            protocols = dict(analysis['protocol_distribution'])
            print(f"  Protocol Summary:    {protocols}")
            
            if analysis['issues']:
                print(f"  Issues Found:        {len(analysis['issues'])}")
                for issue in analysis['issues'][:5]:
                    print(f"    - {issue}")
                if len(analysis['issues']) > 5:
                    print(f"    ... and {len(analysis['issues']) - 5} more")
    
    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze routing tables from network devices.",
    )
    parser.add_argument(
        "--devices",
        type=str,
        default="all",
        help="Device names comma-separated or 'all' (default: all)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Perform route analysis and display report",
    )
    parser.add_argument(
        "--check-coverage",
        type=str,
        metavar="PREFIX",
        help="Check if prefix is covered (e.g., 10.0.0.0/8)",
    )
    parser.add_argument(
        "--export",
        type=str,
        metavar="FILE",
        help="Export routes to JSON file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.devices != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name__in=device_list)
        
        logger.info(f"Collecting routes from {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=get_routing_table)
        
        routes_data = {}
        for device_name, task_result in results.items():
            if not task_result[0].failed:
                routes_data[device_name] = task_result[0].result
        
        logger.info(f"Successfully retrieved routes from {len(routes_data)} device(s)")
        
        if args.analyze:
            analysis = {
                device: analyze_routes(routes)
                for device, routes in routes_data.items()
            }
            print_analysis_report(analysis)
        
        if args.check_coverage:
            print(f"\nCoverage check for {args.check_coverage}:")
            for device, routes in sorted(routes_data.items()):
                result = check_coverage(routes, args.check_coverage)
                if "error" in result:
                    print(f"  {device}: {result['error']}")
                else:
                    status = "✓ COVERED" if result["covered"] else "✗ NOT COVERED"
                    routes_str = ", ".join(result["covering_routes"]) or "None"
                    print(f"  {device}: {status} by [{routes_str}]")
        
        if args.export:
            with open(args.export, "w") as f:
                json.dump(routes_data, f, indent=2, default=str)
            logger.info(f"Routes exported to {args.export}")
        
        if not any([args.analyze, args.check_coverage, args.export]):
            print("No action specified. Use --help for options.")
            return 1
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
```