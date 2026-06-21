```python
"""
Route Summary Report

Gathers routing table information from network devices and generates a
summary report showing total routes, routes by protocol, and optional
filtering by VRF or protocol type.

Prerequisites:
- Nornir inventory configured with network device hosts
- Devices must support 'show ip route' or equivalent command
- SSH/Telnet connectivity to devices

Usage:
    python route_summary.py --devices all --protocol bgp
    python route_summary.py --devices switch1,switch2 --vrf mgmt
    python route_summary.py --devices 192.168.1.1 --username admin --password pass
"""

import argparse
import logging
from typing import Dict, Optional
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.plugins.tasks.networking import netmiko_send_command


logger = logging.getLogger(__name__)


def parse_routing_table(output: str) -> Dict[str, int]:
    """Parse routing table output and count routes by protocol."""
    routes = {"total": 0, "ospf": 0, "bgp": 0, "static": 0, "connected": 0}
    
    if not output:
        return routes
    
    for line in output.split("\n"):
        line = line.strip()
        if not line or line.startswith(("Gateway", "Codes")):
            continue
        
        if line and line[0] in ("O", "B", "S", "C", "R", "E", "*"):
            routes["total"] += 1
            if line.startswith("O"):
                routes["ospf"] += 1
            elif line.startswith("B"):
                routes["bgp"] += 1
            elif line.startswith("S"):
                routes["static"] += 1
            elif line.startswith("C"):
                routes["connected"] += 1
    
    return routes


def get_route_summary(
    task: Task, vrf: Optional[str] = None, protocol: Optional[str] = None
) -> Result:
    """Retrieve and summarize routing table from device."""
    cmd = "show ip route"
    if vrf:
        cmd += f" vrf {vrf}"
    if protocol:
        cmd += f" {protocol}"
    
    try:
        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result if result else ""
        summary = parse_routing_table(output)
        
        return Result(
            host=task.host,
            result={"command": cmd, "summary": summary, "output_lines": len(output.split("\n"))}
        )
    except Exception as e:
        logger.error(f"Error on {task.host.name}: {e}")
        return Result(host=task.host, failed=True, result=str(e))


def main():
    parser = argparse.ArgumentParser(
        description="Generate routing table summary from network devices"
    )
    parser.add_argument(
        "--devices", default="all", help="Comma-separated device names or 'all'"
    )
    parser.add_argument("--username", help="Device username")
    parser.add_argument("--password", help="Device password")
    parser.add_argument("--vrf", help="Filter by VRF name")
    parser.add_argument(
        "--protocol",
        choices=["bgp", "ospf", "rip", "eigrp", "static"],
        help="Filter by routing protocol"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    
    try:
        nr = InitNornir(config_file="config.yaml")
        
        if args.devices != "all":
            device_list = [d.strip() for d in args.devices.split(",")]
            nr = nr.filter(name__in=device_list)
        
        if args.username:
            for host in nr.inventory.hosts.values():
                host.username = args.username
        if args.password:
            for host in nr.inventory.hosts.values():
                host.password = args.password
        
        logger.info(f"Executing route summary on {len(nr.inventory.hosts)} devices")
        
        results = nr.run(
            task=get_route_summary, vrf=args.vrf, protocol=args.protocol
        )
        
        print("\n" + "=" * 70)
        print("ROUTING TABLE SUMMARY REPORT")
        print("=" * 70)
        
        for host_name, host_results in results.items():
            res = host_results[0]
            if res.failed:
                print(f"\n[FAILED] {host_name}: {res.result}")
            else:
                summary = res.result["summary"]
                print(f"\n{host_name}:")
                print(f"  Command:         {res.result['command']}")
                print(f"  Total Routes:    {summary['total']}")
                print(f"  OSPF:            {summary['ospf']}")
                print(f"  BGP:             {summary['bgp']}")
                print(f"  Static:          {summary['static']}")
                print(f"  Connected:       {summary['connected']}")
        
        print("\n" + "=" * 70)
        return 0
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```