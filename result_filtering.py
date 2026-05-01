```python
"""
Route Analysis with Result Filtering - Collect and filter routing table entries.

Connects to network devices via Nornir, collects routing information using netmiko,
and filters results based on prefix, metric, protocol, or next-hop. Useful for
verifying route propagation, identifying unexpected routes, and auditing routing
tables across the network.

Usage:
    python 005_route_filter.py --hosts hosts.yaml
    python 005_route_filter.py --hosts hosts.yaml --prefix 10.0
    python 005_route_filter.py --hosts hosts.yaml --protocol bgp --metric 100
    python 005_route_filter.py --hosts hosts.yaml --exclude internet-edge

Prerequisites:
    - hosts.yaml file with device inventory
    - Nornir installed (pip install nornir netmiko)
    - Device connectivity (SSH/Telnet configured)
    - Supported platforms: Cisco IOS, IOS-XE, Arista EOS

Example hosts.yaml:
    devices:
      router1:
        hostname: 192.168.1.1
        groups:
          - cisco_ios
        data:
          role: "core"
      router2:
        hostname: 192.168.1.2
        groups:
          - cisco_ios
        data:
          role: "distribution"
    groups:
      cisco_ios:
        platform: ios
        port: 22
        username: admin
        password: password
"""

import logging
import argparse
import re
from typing import Dict, List, Any, Optional
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import netmiko_send_command


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging with timestamp and level."""
    logger = logging.getLogger(__name__)
    logger.setLevel(getattr(logging, level))
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    if logger.handlers:
        logger.handlers.clear()
    logger.addHandler(handler)
    return logger


def parse_ios_routes(output: str) -> List[Dict[str, Any]]:
    """Parse Cisco IOS 'show ip route' output into structured format."""
    routes = []
    lines = output.split("\n")
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith("Gateway") or "Codes:" in line:
            continue
        
        # Match common route patterns: C (connected), S (static), B (BGP), etc.
        match = re.match(
            r"([CSOBEIDR*])\s+(\S+)(?:\s+via\s+(\S+))?\s+(\[.*?\])?\s+(.*?)$",
            line
        )
        if match:
            protocol = match.group(1)
            destination = match.group(2)
            gateway = match.group(3) or "direct"
            metric = match.group(4) or ""
            interface = match.group(5) or ""
            
            routes.append({
                "protocol": protocol,
                "destination": destination,
                "gateway": gateway,
                "metric": metric,
                "interface": interface
            })
    
    return routes


def parse_eos_routes(output: str) -> List[Dict[str, Any]]:
    """Parse Arista EOS 'show ip route' output into structured format."""
    routes = []
    lines = output.split("\n")
    
    for line in lines:
        line = line.strip()
        if not line or "Codes:" in line or "Routing Table" in line:
            continue
        
        # EOS format: B    10.0.0.0/24 [200/100] via 192.168.1.1, Ethernet1
        match = re.match(
            r"([CSOBEIDR])\s+(\S+)(?:\s+\[(.*?)\])?\s+via\s+(\S+)(?:,\s+(\S+))?",
            line
        )
        if match:
            protocol = match.group(1)
            destination = match.group(2)
            metric = match.group(3) or ""
            gateway = match.group(4)
            interface = match.group(5) or ""
            
            routes.append({
                "protocol": protocol,
                "destination": destination,
                "gateway": gateway,
                "metric": metric,
                "interface": interface
            })
    
    return routes


def get_routes(task) -> Dict[str, Any]:
    """Collect routing table from device."""
    device = {
        "hostname": task.host.hostname,
        "device": task.host.name,
        "platform": task.host.platform,
        "routes": [],
        "error": None
    }
    
    try:
        if task.host.platform in ["ios", "iosxe"]:
            cmd = "show ip route"
            result = task.run(
                name="Gather routes",
                task=netmiko_send_command,
                command_string=cmd
            )
            device["routes"] = parse_ios_routes(result.result)
        
        elif task.host.platform == "eos":
            cmd = "show ip route"
            result = task.run(
                name="Gather routes",
                task=netmiko_send_command,
                command_string=cmd
            )
            device["routes"] = parse_eos_routes(result.result)
        
        else:
            device["error"] = f"Platform {task.host.platform} not supported"
    
    except Exception as e:
        device["error"] = str(e)
    
    return device


def filter_routes(
    routes: List[Dict[str, Any]],
    prefix: Optional[str] = None,
    protocol: Optional[str] = None,
    metric: Optional[str] = None,
    gateway: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Filter routes by multiple criteria."""
    filtered = routes
    
    if prefix:
        filtered = [r for r in filtered if prefix in r["destination"]]
    
    if protocol:
        filtered = [r for r in filtered if r["protocol"].lower() == protocol.lower()]
    
    if metric and metric.isdigit():
        filtered = [r for r in filtered if str(metric) in r["metric"]]
    
    if gateway:
        filtered = [r for r in filtered if gateway in r["gateway"]]
    
    return filtered


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Collect and filter routing tables from network devices"
    )
    parser.add_argument(
        "--hosts",
        type=str,
        default="hosts.yaml",
        help="Path to Nornir hosts inventory file"
    )
    parser.add_argument(
        "--prefix",
        type=str,
        help="Filter routes by destination prefix (e.g., '10.0')"
    )
    parser.add_argument(
        "--protocol",
        type=str,
        choices=["C", "S", "B", "O", "E", "I", "D", "R"],
        help="Filter by routing protocol code (C=connected, S=static, B=BGP, O=OSPF, etc.)"
    )
    parser.add_argument(
        "--metric",
        type=str,
        help="Filter by route metric (e.g., '100')"
    )
    parser.add_argument(
        "--gateway",
        type=str,
        help="Filter by next-hop gateway IP"
    )
    parser.add_argument(
        "--exclude",
        type=str,
        nargs="+",
        help="Device names/patterns to exclude"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level"
    )
    
    args = parser.parse_args()
    logger = setup_logging(args.log_level)
    
    logger.info("Initializing Nornir from %s", args.hosts)
    try:
        nr = InitNornir(config_file=args.hosts)
    except Exception as e:
        logger.error("Failed to initialize Nornir: %s", e)
        return 1
    
    exclude_list = args.exclude or []
    if exclude_list:
        nr = nr.filter(~F(name__contains=exclude_list[0]))
        for pattern in exclude_list[1:]:
            nr = nr.filter(~F(name__contains=pattern))
        logger.info("Applied exclusion filters: %s", exclude_list)
    
    logger.info("Executing route collection on %d device(s)", len(nr.inventory.hosts))
    results = nr.run(task=get_routes)
    
    # Aggregate results
    total_routes = 0
    filtered_routes = 0
    
    logger.info("\n" + "=" * 80)
    logger.info("ROUTE ANALYSIS REPORT")
    logger.info("=" * 80)
    
    for host_name, task_result in results.items():
        if not task_result.ok:
            logger.error("%s [FAILED]", host_name)
            continue
        
        device_data = task_result[0].result
        
        if device_data["error"]:
            logger.error("%s [ERROR] %s", host_name, device_data["error"])
            continue
        
        # Filter routes for this device
        filtered = filter_routes(
            device_data["routes"],
            args.prefix,
            args.protocol,
            args.metric,
            args.gateway
        )
        
        total_routes += len(device_data["routes"])
        filtered_routes += len(filtered)
        
        logger.info("\n%s: %d routes (showing %d after filters)",
                   host_name, len(device_data["routes"]), len(filtered))
        
        for route in filtered:
            logger.info("  [%s] %s via %s %s",
                       route["protocol"],
                       route["destination"],
                       route["gateway"],
                       route["metric"])
    
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY: %d total routes, %d matched filters",
               total_routes, filtered_routes)
    logger.info("=" * 80)
    
    return 0


if __name__ == "__main__":
    exit(main())
```