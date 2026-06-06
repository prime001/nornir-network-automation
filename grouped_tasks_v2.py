```python
"""
Route Table Analyzer - Analyze and report on device routing tables.

Purpose:
    Connects to network devices and gathers routing table information,
    analyzing route statistics by protocol, identifying default routes,
    and generating comprehensive routing reports for network auditing.

Usage:
    python route_analyzer.py --devices r1,r2,r3 --username admin --password pass
    python route_analyzer.py --devices prod_routers --format json --output routes.json
    python route_analyzer.py --devices all --check-overlaps --verbose

Prerequisites:
    - Nornir installed and configured with inventory
    - Network device SSH access with appropriate credentials
    - napalm library available (pip install napalm)
    - Devices running IOS, IOS-XE, Junos, or EOS

Arguments:
    --devices       Comma-separated device names or inventory group name
    --username      SSH username (optional if set in inventory)
    --password      SSH password (optional if set in inventory)
    --format        Output format: json or text (default: text)
    --output        Output file path (default: stdout)
    --check-overlaps Check for overlapping routes across devices
    --verbose       Enable verbose debug logging
"""

import argparse
import json
import logging
from collections import defaultdict
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


def setup_logging(verbose):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    return logging.getLogger(__name__)


def get_routes(task):
    """Retrieve routing table from device."""
    try:
        result = task.run(napalm_get, getters=["route_table"])
        if result[0].result:
            return result[0].result.get("route_table", {})
    except Exception as e:
        task.host.log(f"Failed to get routes: {e}")
    return None


def analyze_routes(routes):
    """Generate route statistics."""
    stats = {
        "total": 0,
        "by_protocol": defaultdict(int),
        "default_routes": [],
        "host_routes": 0,
    }
    
    if not routes:
        return stats
    
    for vrf, route_list in routes.items():
        for route in route_list:
            stats["total"] += 1
            protocol = route.get("protocol", "unknown").upper()
            stats["by_protocol"][protocol] += 1
            
            destination = route.get("destination", "")
            if destination in ("0.0.0.0/0", "::/0"):
                stats["default_routes"].append({
                    "destination": destination,
                    "next_hop": route.get("next_hop", ""),
                    "protocol": protocol,
                })
            elif "/32" in destination or "/128" in destination:
                stats["host_routes"] += 1
    
    return stats


def check_overlaps(all_routes):
    """Identify overlapping routes."""
    overlaps = []
    subnets = defaultdict(list)
    
    for device, routes in all_routes.items():
        if not routes:
            continue
        for vrf, route_list in routes.items():
            for route in route_list:
                subnet = route.get("destination", "")
                subnets[subnet].append({
                    "device": device,
                    "next_hop": route.get("next_hop", "")
                })
    
    return [{"subnet": s, "devices": d} for s, d in subnets.items() if len(d) > 1]


def format_text(analysis, overlaps):
    """Format output as text."""
    lines = ["\n" + "=" * 70, "ROUTING TABLE ANALYSIS", "=" * 70 + "\n"]
    
    for device, data in analysis.items():
        stats = data["stats"]
        lines.append(f"Device: {device}")
        lines.append(f"  Total Routes: {stats['total']}")
        lines.append(f"  Default Routes: {len(stats['default_routes'])}")
        lines.append(f"  Host Routes: {stats['host_routes']}")
        lines.append("  By Protocol:")
        for protocol, count in sorted(stats["by_protocol"].items()):
            lines.append(f"    {protocol}: {count}")
        lines.append("")
    
    if overlaps:
        lines.append("OVERLAPPING ROUTES\n")
        for overlap in overlaps[:10]:
            lines.append(f"  {overlap['subnet']}: {len(overlap['devices'])} devices")
    
    return "\n".join(lines)


def format_json(analysis, overlaps):
    """Format output as JSON."""
    return json.dumps({
        "devices": {
            device: {
                "total_routes": data["stats"]["total"],
                "by_protocol": dict(data["stats"]["by_protocol"]),
                "default_routes": data["stats"]["default_routes"],
                "host_routes": data["stats"]["host_routes"]
            }
            for device, data in analysis.items()
        },
        "overlapping_subnets": len(overlaps or [])
    }, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze routing tables across network devices"
    )
    parser.add_argument("--devices", required=True,
                        help="Device names (comma-separated) or group")
    parser.add_argument("--username", help="SSH username")
    parser.add_argument("--password", help="SSH password")
    parser.add_argument("--format", choices=["json", "text"],
                        default="text", help="Output format")
    parser.add_argument("--output", help="Output file")
    parser.add_argument("--check-overlaps", action="store_true",
                        help="Check for overlapping routes")
    parser.add_argument("--verbose", action="store_true")
    
    args = parser.parse_args()
    logger = setup_logging(args.verbose)
    
    try:
        nr = InitNornir()
        devices = args.devices.split(",")
        hosts = nr.filter(F(name__in=devices))
        
        if not hosts:
            logger.error(f"No devices found: {args.devices}")
            return 1
        
        logger.info(f"Gathering routes from {len(hosts.inventory.hosts)} devices")
        results = hosts.run(task=get_routes)
        
        analysis = {}
        all_routes = {}
        
        for device_name, result in results.items():
            routes = result[0].result if result[0].result else {}
            all_routes[device_name] = routes
            analysis[device_name] = {"stats": analyze_routes(routes)}
        
        overlaps = check_overlaps(all_routes) if args.check_overlaps else None
        
        if args.format == "json":
            output = format_json(analysis, overlaps)
        else:
            output = format_text(analysis, overlaps)
        
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            logger.info(f"Output written to {args.output}")
        else:
            print(output)
        
        return 0
    
    except Exception as e:
        logger.error(f"Script failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```