```python
#!/usr/bin/env python3
"""
BGP Neighbor Health Monitor

Purpose:
  Monitor BGP neighbor health and stability across network devices.
  Detects neighbor state transitions, flapping, and connectivity issues.
  Aggregates statistics for network-wide BGP health visibility.

Usage:
  python bgp_neighbor_monitor.py -i inventory/ --hosts router1,router2
  python bgp_neighbor_monitor.py -i inventory/ -o json > bgp_health.json

Prerequisites:
  - Nornir inventory with BGP-enabled devices
  - Device credentials via inventory or environment variables
  - Devices supporting netmiko (Cisco IOS, Arista, Juniper, etc.)

Output:
  - Neighbor state summary with uptime and message counts
  - Anomaly detection (down neighbors, state changes)
  - JSON export option for integration with monitoring systems
"""

import argparse
import json
import logging
from typing import Any, Dict

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import netmiko_send_command


def setup_logging(level: str) -> logging.Logger:
    """Configure logging with timestamp and level."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    return logging.getLogger(__name__)


def get_bgp_neighbors(task: Task) -> Result:
    """
    Retrieve BGP neighbor information from device using netmiko.
    Supports multiple vendor formats (Cisco, Arista, Juniper).
    """
    device_type = task.host.get("device_type", "")
    
    if "juniper" in device_type.lower():
        cmd = "show bgp neighbor"
    else:
        cmd = "show ip bgp neighbors"
    
    try:
        result = task.run(netmiko_send_command, command_string=cmd)
        return result
    except Exception as e:
        return Result(host=task.host, failed=True, result=f"Error: {e}")


def parse_cisco_neighbors(output: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse Cisco IOS/IOS-XE BGP neighbor output.
    Extracts IP, state, uptime, and message counts.
    """
    neighbors = {}
    current_neighbor = None
    
    for line in output.split("\n"):
        line = line.strip()
        
        if line.startswith("BGP neighbor is"):
            parts = line.split()
            neighbor_ip = parts[-1].rstrip(",")
            current_neighbor = {
                "ip": neighbor_ip,
                "state": "Unknown",
                "uptime": "N/A",
                "messages_sent": "0",
                "messages_received": "0"
            }
            neighbors[neighbor_ip] = current_neighbor
        
        elif current_neighbor:
            if line.startswith("BGP state"):
                current_neighbor["state"] = line.split("=")[-1].strip()
            elif "Up/Down" in line:
                parts = line.split()
                current_neighbor["uptime"] = parts[-1]
            elif "Sent" in line and "Received" in line:
                parts = line.split()
                if len(parts) >= 4:
                    current_neighbor["messages_sent"] = parts[1]
                    current_neighbor["messages_received"] = parts[3]
    
    return neighbors


def analyze_neighbors(neighbors: Dict[str, Dict]) -> Dict[str, Any]:
    """
    Analyze neighbor data and detect anomalies.
    Returns summary statistics and issues.
    """
    analysis = {
        "total": len(neighbors),
        "established": 0,
        "down": 0,
        "other": 0,
        "issues": []
    }
    
    for ip, data in neighbors.items():
        state = data.get("state", "").lower()
        
        if "established" in state:
            analysis["established"] += 1
        elif "down" in state:
            analysis["down"] += 1
            analysis["issues"].append(f"Neighbor {ip} is DOWN")
        else:
            analysis["other"] += 1
            analysis["issues"].append(f"Neighbor {ip} in {state} state")
    
    return analysis


def display_summary(device_results: Dict[str, Any]) -> None:
    """
    Display formatted neighbor health summary to console.
    """
    print("\n" + "=" * 70)
    print("BGP Neighbor Health Monitor - Summary Report")
    print("=" * 70)
    
    total_neighbors = 0
    total_established = 0
    total_down = 0
    all_issues = []
    
    for device_name, device_data in device_results.items():
        if device_data.get("error"):
            print(f"\n[{device_name}] ERROR: {device_data['error']}")
            continue
        
        analysis = device_data.get("analysis", {})
        neighbors = device_data.get("neighbors", {})
        
        total_neighbors += analysis.get("total", 0)
        total_established += analysis.get("established", 0)
        total_down += analysis.get("down", 0)
        all_issues.extend(device_data.get("device_issues", []))
        
        print(f"\n[{device_name}]")
        print(f"  Neighbors: {analysis.get('total', 0)} total")
        print(f"    Established: {analysis.get('established', 0)}")
        print(f"    Down: {analysis.get('down', 0)}")
        print(f"    Other: {analysis.get('other', 0)}")
        
        if neighbors:
            print("  Neighbor Details:")
            for ip, data in neighbors.items():
                state = data.get("state", "Unknown")
                uptime = data.get("uptime", "N/A")
                print(f"    {ip}: {state} (up {uptime})")
    
    print("\n" + "-" * 70)
    print(f"Network Total: {total_neighbors} neighbors")
    print(f"  Established: {total_established}")
    print(f"  Down: {total_down}")
    
    if all_issues:
        print("\nAnomalies Detected:")
        for issue in all_issues:
            print(f"  ⚠ {issue}")
    else:
        print("\n✓ No anomalies detected")
    
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor BGP neighbor health across network",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: python bgp_neighbor_monitor.py -i inventory/ --hosts rtr1,rtr2"
    )
    parser.add_argument(
        "-i", "--inventory",
        default="inventory/",
        help="Path to nornir inventory directory (default: inventory/)"
    )
    parser.add_argument(
        "--hosts",
        help="Comma-separated list of hosts to monitor (optional: all if omitted)"
    )
    parser.add_argument(
        "-o", "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-l", "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)"
    )
    
    args = parser.parse_args()
    logger = setup_logging(args.log_level)
    
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
        
        if args.hosts:
            hosts = [h.strip() for h in args.hosts.split(",")]
            nr = nr.filter(name__in=hosts)
        
        logger.info(f"Monitoring BGP neighbors on {len(nr.inventory.hosts)} device(s)")
        
        results = nr.run(task=get_bgp_neighbors)
        
        device_results = {}
        for device_name, task_result in results.items():
            if task_result.failed:
                device_results[device_name] = {"error": str(task_result.result)}
            else:
                neighbors = parse_cisco_neighbors(task_result.result)
                analysis = analyze_neighbors(neighbors)
                device_results[device_name] = {
                    "neighbors": neighbors,
                    "analysis": analysis,
                    "device_issues": analysis.pop("issues", [])
                }
        
        if args.output_format == "json":
            print(json.dumps(device_results, indent=2, default=str))
        else:
            display_summary(device_results)
        
        return 0
    
    except Exception as e:
        logger.error(f"BGP monitor failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
```