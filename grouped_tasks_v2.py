```python
#!/usr/bin/env python3
"""
Device Health Monitor - Network device CPU, memory, and uptime tracking.

Monitors critical health metrics across network devices and generates health
reports with threshold-based alerting for operational compliance.

Usage:
    python device_health_monitor.py --inventory inventory.yaml --threshold-cpu 80

Prerequisites:
    - nornir installed with netmiko plugin
    - Device inventory configured with device_type (ios, eos, junos, etc.)
    - Device credentials in environment or inventory

Output:
    - Console report with health status per device
    - Exit code 1 if thresholds exceeded, 0 if healthy
"""

import argparse
import logging
import sys
from typing import Dict, Any

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import netmiko_send_command


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def parse_metrics(device_type: str, show_version: str, show_cpu: str) -> Dict[str, Any]:
    """Extract CPU, memory, and uptime from device output."""
    metrics = {"cpu": None, "memory": None, "uptime": None}
    
    combined = show_version + "\n" + show_cpu
    for line in combined.split("\n"):
        if "uptime is" in line.lower():
            metrics["uptime"] = line.strip()
        
        if "cpu" in line.lower() and "%" in line:
            parts = line.split()
            for part in parts:
                if "%" in part:
                    try:
                        metrics["cpu"] = float(part.rstrip("%"))
                        break
                    except ValueError:
                        pass
        
        if "memory" in line.lower() and "%" in line:
            parts = line.split()
            for part in parts:
                if "%" in part:
                    try:
                        metrics["memory"] = float(part.rstrip("%"))
                        break
                    except ValueError:
                        pass
    
    return metrics


def collect_health(task: Task, cpu_threshold: float, mem_threshold: float) -> Result:
    """Collect health metrics from device."""
    host = task.host
    
    try:
        version_resp = task.run(netmiko_send_command, command_string="show version")
        version_out = version_resp[0].result if version_resp else ""
        
        cpu_cmd = (
            "show processes cpu | include CPU utilization"
            if "ios" in host.device_type.lower()
            else "show system resources | grep -i cpu"
        )
        
        cpu_resp = task.run(netmiko_send_command, command_string=cpu_cmd)
        cpu_out = cpu_resp[0].result if cpu_resp else ""
        
        metrics = parse_metrics(host.device_type, version_out, cpu_out)
        
        alerts = []
        if metrics["cpu"] is not None and metrics["cpu"] > cpu_threshold:
            alerts.append(f"CPU: {metrics['cpu']:.1f}% (threshold: {cpu_threshold}%)")
        if metrics["memory"] is not None and metrics["memory"] > mem_threshold:
            alerts.append(f"Memory: {metrics['memory']:.1f}% (threshold: {mem_threshold}%)")
        
        return Result(
            host=host,
            result={
                "hostname": host.name,
                "device_type": host.device_type,
                "cpu": metrics["cpu"],
                "memory": metrics["memory"],
                "uptime": metrics["uptime"],
                "status": "CRITICAL" if alerts else "HEALTHY",
                "alerts": alerts,
            },
        )
    except Exception as e:
        logging.error(f"Error collecting health for {host.name}: {e}")
        return Result(
            host=host,
            result={"hostname": host.name, "status": "ERROR", "error": str(e)},
            failed=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor network device health metrics",
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to nornir inventory file",
    )
    parser.add_argument(
        "--threshold-cpu",
        type=float,
        default=80.0,
        help="CPU threshold percentage (default: 80)",
    )
    parser.add_argument(
        "--threshold-memory",
        type=float,
        default=85.0,
        help="Memory threshold percentage (default: 85)",
    )
    parser.add_argument(
        "--group",
        help="Filter devices by inventory group",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        if args.group:
            nr = nr.filter(group=args.group)
        
        logger.info(f"Monitoring {len(nr.inventory.hosts)} devices")
        
        results = nr.run(
            task=collect_health,
            cpu_threshold=args.threshold_cpu,
            mem_threshold=args.threshold_memory,
            num_workers=4,
        )
        
        print("\n" + "=" * 80)
        print("DEVICE HEALTH REPORT")
        print("=" * 80)
        
        critical = error = 0
        
        for host in sorted(results.keys()):
            data = results[host][0].result
            status = data.get("status", "UNKNOWN")
            
            if status == "ERROR":
                error += 1
                print(f"\n{host}: ERROR - {data.get('error')}")
            else:
                if status == "CRITICAL":
                    critical += 1
                
                print(f"\n{host}: {status}")
                print(f"  Device Type: {data.get('device_type')}")
                
                if data.get("cpu") is not None:
                    print(f"  CPU: {data['cpu']:.1f}%")
                if data.get("memory") is not None:
                    print(f"  Memory: {data['memory']:.1f}%")
                if data.get("uptime"):
                    print(f"  Uptime: {data['uptime']}")
                
                for alert in data.get("alerts", []):
                    print(f"  ALERT: {alert}")
        
        print("\n" + "=" * 80)
        print(f"Summary: {critical} critical, {error} errors out of {len(nr.inventory.hosts)} devices")
        print("=" * 80)
        
        return 1 if (critical > 0 or error > 0) else 0
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```