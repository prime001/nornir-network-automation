```python
"""
Route Validator - Verify specific routes exist on network devices

Purpose:
    Validates that an IP route exists on target devices, showing route
    details (prefix, next hop, metric, source). Useful for troubleshooting
    route propagation and verifying BGP/OSPF convergence.

Usage:
    python route_validator.py -r "10.1.0.0/16" -i inventory.yaml

Prerequisites:
    - Nornir installed and configured
    - Network devices reachable via SSH
    - Device credentials configured (via inventory, netrc, or .env)
    - Device support: Cisco IOS/IOS-XE, Arista, Juniper

Examples:
    # Validate route on specific devices
    python route_validator.py -r "10.0.0.0/8" --devices core-1,core-2

    # Save results to JSON
    python route_validator.py -r "192.168.1.0/24" --output routes.json
"""

import argparse
import json
import logging
from typing import Dict

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_route(task: Task, target_route: str) -> Result:
    """
    Check if a route exists on device and return details.
    
    Args:
        task: Nornir task
        target_route: CIDR notation route (e.g., "10.1.0.0/16")
    
    Returns:
        Result with route details or not-found message
    """
    device = task.host
    platform = device.platform or "unknown"
    
    if platform.startswith("cisco"):
        cmd = "show ip route"
    elif platform.startswith("arista"):
        cmd = "show ip route"
    elif platform.startswith("juniper"):
        cmd = "show route"
    else:
        return Result(
            host=device,
            failed=True,
            result=f"Unsupported platform: {platform}"
        )
    
    try:
        output_result = task.run(
            netmiko_send_command,
            command_string=cmd
        )
        output_text = output_result[0].result
        
        route_prefix = target_route.split('/')[0]
        found_lines = []
        
        for line in output_text.split('\n'):
            if route_prefix in line and any(
                proto in line.upper()
                for proto in ['BGP', 'OSPF', 'RIP', 'STATIC', 'CONNECTED', 'C']
            ):
                found_lines.append(line.strip())
        
        if found_lines:
            return Result(
                host=device,
                result={
                    "route": target_route,
                    "status": "FOUND",
                    "details": found_lines
                }
            )
        else:
            return Result(
                host=device,
                result={
                    "route": target_route,
                    "status": "NOT_FOUND",
                    "details": []
                }
            )
    
    except Exception as e:
        logger.error(f"Error on {device.name}: {str(e)}")
        return Result(host=device, failed=True, result=str(e))


def format_output(results: Dict, fmt: str) -> str:
    """Format results as text or JSON."""
    if fmt == "json":
        output = {}
        for host, result in results.items():
            if result[0].failed:
                output[host] = {"error": result[0].result}
            else:
                output[host] = result[0].result
        return json.dumps(output, indent=2)
    
    lines = []
    for host, result in results.items():
        lines.append(f"\n{host}:")
        if result[0].failed:
            lines.append(f"  ERROR: {result[0].result}")
        else:
            data = result[0].result
            route = data["route"]
            if data["status"] == "FOUND":
                lines.append(f"  ✓ Route: {route}")
                for detail in data["details"]:
                    lines.append(f"    {detail}")
            else:
                lines.append(f"  ✗ Route not found: {route}")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Validate IP routes across network devices"
    )
    parser.add_argument(
        "-r", "--route",
        required=True,
        help="CIDR route to validate (e.g., 10.1.0.0/16)"
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory.yaml",
        help="Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "-d", "--devices",
        help="Comma-separated device names (default: all)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Write results to file (optional)"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return 1
    
    if args.devices:
        device_list = args.devices.split(",")
        nr = nr.filter(name__in=device_list)
    
    if len(nr.inventory.hosts) == 0:
        logger.error("No devices selected")
        return 1
    
    logger.info(
        f"Validating route {args.route} on "
        f"{len(nr.inventory.hosts)} device(s)"
    )
    
    results = nr.run(task=validate_route, target_route=args.route)
    
    output = format_output(results, args.format)
    
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
        logger.info(f"Results saved to {args.output}")
    else:
        print(output)
    
    return 0


if __name__ == "__main__":
    exit(main())
```