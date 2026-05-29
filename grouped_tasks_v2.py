```python
#!/usr/bin/env python3
"""
Device Configuration Line Analyzer

Analyzes network device configurations to measure complexity, identify enabled
features, and track configuration baselines. Useful for capacity planning,
change impact analysis, and configuration governance.

Prerequisites:
    - Nornir with netmiko transport
    - Network device SSH access
    - hosts.yml and groups.yml in inventory/

Usage:
    python config_line_analyzer.py --group core-routers
    python config_line_analyzer.py --group switches --format json -o analysis.json
    python config_line_analyzer.py --host router1 --search "bgp" --format table
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


def analyze_configuration(task: Task, search_terms: List[str] = None) -> Result:
    """
    Retrieve and analyze device configuration.
    
    Counts configuration lines, identifies features, searches for keywords.
    """
    try:
        config_result = task.run(
            netmiko_send_command,
            command_string="show running-config",
            use_textfsm=False,
            name="Retrieve Configuration"
        )
        
        config = config_result.result
        lines = [line.strip() for line in config.split('\n') if line.strip()]
        
        # Count non-comment lines
        non_comment_lines = [
            line for line in lines
            if not line.startswith('!')
        ]
        
        # Extract key features
        features = {
            'bgp': any('bgp' in line.lower() for line in lines),
            'ospf': any('ospf' in line.lower() for line in lines),
            'eigrp': any('eigrp' in line.lower() for line in lines),
            'vlan': any('vlan' in line.lower() for line in lines),
            'acl': any('access-list' in line.lower() for line in lines),
            'nat': any('nat' in line.lower() for line in lines),
            'ipsec': any('ipsec' in line.lower() or 'crypto' in line.lower() for line in lines),
            'qos': any('qos' in line.lower() or 'policy-map' in line.lower() for line in lines),
        }
        
        # Search for custom terms
        search_results = {}
        if search_terms:
            for term in search_terms:
                matches = [
                    line for line in lines
                    if term.lower() in line.lower()
                ]
                search_results[term] = len(matches)
        
        result = {
            'device': task.host.name,
            'total_lines': len(lines),
            'config_lines': len(non_comment_lines),
            'comment_lines': len(lines) - len(non_comment_lines),
            'features_enabled': {k: v for k, v in features.items() if v},
            'features_count': sum(1 for v in features.values() if v),
        }
        
        if search_results:
            result['search_matches'] = search_results
        
        return Result(host=task.host, result=result)
        
    except Exception as e:
        logging.error(f"Configuration analysis failed for {task.host.name}: {e}")
        return Result(
            host=task.host,
            result={'device': task.host.name, 'error': str(e)},
            failed=True
        )


def print_table(data: List[Dict[str, Any]]) -> None:
    """Print analysis results as formatted table."""
    print("\n" + "=" * 100)
    print(
        f"{'Device':<20} {'Total Lines':<15} {'Config Lines':<15} "
        f"{'Features':<15} {'BGP':<8} {'OSPF':<8} {'VLAN':<8}"
    )
    print("=" * 100)
    
    for item in data:
        if 'error' in item:
            print(f"{item['device']:<20} ERROR: {item['error']:<70}")
            continue
        
        features = item.get('features_enabled', {})
        bgp = '✓' if features.get('bgp') else '-'
        ospf = '✓' if features.get('ospf') else '-'
        vlan = '✓' if features.get('vlan') else '-'
        
        print(
            f"{item['device']:<20} {item['total_lines']:<15} "
            f"{item['config_lines']:<15} {item['features_count']:<15} "
            f"{bgp:<8} {ospf:<8} {vlan:<8}"
        )
    
    print("=" * 100)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze network device configurations"
    )
    parser.add_argument(
        "--group",
        help="Filter devices by group"
    )
    parser.add_argument(
        "--host",
        help="Analyze single host only"
    )
    parser.add_argument(
        "--search",
        nargs='+',
        help="Search for specific keywords in configuration"
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Write results to file"
    )
    parser.add_argument(
        "--inventory",
        default="inventory",
        help="Nornir inventory path (default: inventory)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger(__name__)
    
    try:
        # Initialize Nornir
        inventory_path = Path(args.inventory) / "config.yaml"
        if not inventory_path.exists():
            logger.error(f"Inventory config not found: {inventory_path}")
            sys.exit(1)
        
        nr = InitNornir(config_file=str(inventory_path))
        logger.info(f"Loaded {len(nr.inventory.hosts)} hosts")
        
        # Apply filters
        if args.host:
            nr = nr.filter(F(name=args.host))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        
        if not nr.inventory.hosts:
            logger.error("No devices matched filter criteria")
            sys.exit(1)
        
        logger.info(f"Analyzing {len(nr.inventory.hosts)} device(s)")
        
        # Run analysis
        results = nr.run(
            task=analyze_configuration,
            search_terms=args.search or []
        )
        
        # Collect results
        output_data = []
        failed_count = 0
        
        for hostname in results:
            task_result = results[hostname][0]
            output_data.append(task_result.result)
            if task_result.failed:
                failed_count += 1
        
        logger.info(f"Analysis complete: {len(output_data) - failed_count} successful, {failed_count} failed")
        
        # Format and display output
        if args.format == "json":
            output_text = json.dumps(output_data, indent=2)
        else:
            output_text = None
            print_table(output_data)
        
        # Write to file if specified
        if args.output:
            with open(args.output, 'w') as f:
                if output_text:
                    f.write(output_text)
                else:
                    f.write('\n'.join(str(item) for item in output_data))
            logger.info(f"Results saved to {args.output}")
        elif output_text:
            print(output_text)
        
        sys.exit(0 if failed_count == 0 else 1)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```