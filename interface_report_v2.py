```python
#!/usr/bin/env python3
"""
Route Analysis Report - Analyzes and reports on device routing tables.

This script gathers routing information from network devices using nornir,
analyzes route distributions, identifies anomalies, and generates a comprehensive
routing report. Useful for network validation, troubleshooting, and topology
verification.

Usage:
    python route_analysis_report.py --hosts all --username admin --password secret
    python route_analysis_report.py --hosts router1,router2 --username admin --password secret
    python route_analysis_report.py --hosts all --username admin --password secret --format json

Prerequisites:
    - nornir and nornir-netmiko installed
    - Devices configured in inventory (hosts.yaml/groups.yaml)
    - SSH access to target devices with enable/privilege access
"""

import argparse
import json
import logging
import sys
from typing import Dict, List, Any
from collections import defaultdict

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def gather_routes(task: Task) -> Result:
    """Gather routing information from devices using NAPALM."""
    try:
        from nornir_napalm.plugins.tasks import napalm_get
        
        result = task.run(napalm_get, getters=["route_info"])
        routes_data = result[0].result.get("route_info", {})
        
        analysis = analyze_routes(routes_data)
        
        return Result(host=task.host, result={
            "raw_routes": routes_data,
            "analysis": analysis
        })
    
    except Exception as e:
        logger.error(f"Failed to gather routes for {task.host}: {str(e)}")
        return Result(host=task.host, result={"error": str(e)}, failed=True)


def analyze_routes(routes_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze routing table for statistics and anomalies."""
    analysis = {
        "total_routes": 0,
        "by_protocol": defaultdict(int),
        "by_prefix_length": defaultdict(int),
        "default_routes": [],
        "static_routes": 0,
        "ospf_routes": 0,
        "bgp_routes": 0,
        "connected_routes": 0,
    }
    
    if not routes_data:
        return analysis
    
    for vrf, prefixes in routes_data.items():
        if not isinstance(prefixes, dict):
            continue
            
        for prefix, route_info in prefixes.items():
            analysis["total_routes"] += 1
            
            if prefix == "0.0.0.0/0":
                analysis["default_routes"].append({
                    "vrf": vrf,
                    "nexthops": route_info.get("next_hops", [])
                })
            
            protocol = route_info.get("protocol", "unknown")
            analysis["by_protocol"][protocol] += 1
            
            if protocol == "static":
                analysis["static_routes"] += 1
            elif protocol == "ospf":
                analysis["ospf_routes"] += 1
            elif protocol == "bgp":
                analysis["bgp_routes"] += 1
            elif protocol == "connected":
                analysis["connected_routes"] += 1
            
            prefix_len = int(prefix.split("/")[-1]) if "/" in prefix else 0
            analysis["by_prefix_length"][prefix_len] += 1
    
    analysis["by_protocol"] = dict(analysis["by_protocol"])
    analysis["by_prefix_length"] = dict(sorted(analysis["by_prefix_length"].items()))
    
    return analysis


def format_results_text(results: Dict[str, Any]) -> None:
    """Format results as human-readable text."""
    print("\n" + "="*100)
    print(f"{'Device':<20} {'Total':<10} {'BGP':<10} {'OSPF':<10} {'Static':<10} {'Connected':<15}")
    print("="*100)
    
    for host, result in results.items():
        if isinstance(result, list) and result and not result[0].failed:
            analysis = result[0].result.get("analysis", {})
            total = analysis.get("total_routes", 0)
            bgp = analysis.get("bgp_routes", 0)
            ospf = analysis.get("ospf_routes", 0)
            static = analysis.get("static_routes", 0)
            connected = analysis.get("connected_routes", 0)
            
            print(f"{host:<20} {total:<10} {bgp:<10} {ospf:<10} {static:<10} {connected:<15}")
        else:
            print(f"{host:<20} {'ERROR':<10}")
    
    print("="*100)
    
    for host, result in results.items():
        if isinstance(result, list) and result and not result[0].failed:
            analysis = result[0].result.get("analysis", {})
            
            print(f"\n{host} - Route Analysis:")
            print(f"  Total Routes: {analysis.get('total_routes', 0)}")
            
            by_protocol = analysis.get("by_protocol", {})
            if by_protocol:
                print("  Routes by Protocol:")
                for protocol, count in sorted(by_protocol.items()):
                    print(f"    {protocol}: {count}")
            
            defaults = analysis.get("default_routes", [])
            if defaults:
                print(f"  Default Routes: {len(defaults)}")
                for default in defaults:
                    print(f"    VRF: {default['vrf']}, Nexthops: {default['nexthops']}")
            else:
                print("  Default Routes: None")
            
            prefix_lens = analysis.get("by_prefix_length", {})
            if prefix_lens:
                print("  Top 5 Prefix Lengths:")
                for length, count in sorted(prefix_lens.items(), key=lambda x: x[1], reverse=True)[:5]:
                    print(f"    /{length}: {count} routes")


def format_results_json(results: Dict[str, Any]) -> None:
    """Format results as JSON."""
    output = {}
    
    for host, result in results.items():
        if isinstance(result, list) and result:
            if result[0].failed:
                output[host] = {"error": result[0].result.get("error")}
            else:
                output[host] = result[0].result.get("analysis", {})
        else:
            output[host] = {"error": "No result"}
    
    print(json.dumps(output, indent=2, default=str))


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Analyze and report on network device routing tables"
    )
    parser.add_argument(
        "--hosts",
        type=str,
        default="all",
        help="Comma-separated list of hosts or 'all' (default: all)"
    )
    parser.add_argument(
        "--username",
        type=str,
        required=True,
        help="Username for device authentication"
    )
    parser.add_argument(
        "--password",
        type=str,
        required=True,
        help="Password for device authentication"
    )
    parser.add_argument(
        "--inventory",
        type=str,
        default="./inventory",
        help="Path to nornir inventory directory (default: ./inventory)"
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--loglevel",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)"
    )
    
    args = parser.parse_args()
    logger.setLevel(getattr(logging, args.loglevel))
    
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
        
        if args.hosts != "all":
            hosts = [h.strip() for h in args.hosts.split(",")]
            nr = nr.filter(F(name__in=hosts))
        
        if len(nr.inventory.hosts) == 0:
            logger.error("No hosts found matching the filter")
            sys.exit(1)
        
        logger.info(f"Running route analysis on {len(nr.inventory.hosts)} device(s)")
        
        results = nr.run(task=gather_routes)
        
        failed_count = sum(1 for r in results.values() if r[0].failed)
        logger.info(f"Route analysis completed: {len(results) - failed_count} successful, {failed_count} failed")
        
        if args.format == "json":
            format_results_json(dict(results))
        else:
            format_results_text(dict(results))
        
        if failed_count > 0:
            sys.exit(1)
    
    except Exception as e:
        logger.error(f"Failed to run route analysis: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```