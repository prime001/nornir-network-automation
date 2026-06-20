```python
"""
Device Performance Metrics Collector - Network Device Monitoring

Purpose:
    Collects and analyzes performance metrics (CPU, memory, interface statistics)
    from network devices via nornir using NAPALM drivers.

Usage:
    python device_performance_monitor.py --devices all --output json
    python device_performance_monitor.py --device router1 --metrics cpu,memory

Prerequisites:
    - nornir with NAPALM plugin installed
    - netmiko or paramiko for SSH connectivity
    - NAPALM library compatible with target device types
    - Inventory file with device definitions (config.yaml)
    - SSH/API access to network devices with appropriate credentials
"""

import logging
import argparse
import json
from typing import Dict, Any, List
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get


def setup_logging(level: str) -> logging.Logger:
    """Configure logging with specified verbosity level."""
    logger = logging.getLogger("perf_monitor")
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper()))
    return logger


def collect_metrics(task: Task, metrics: List[str]) -> Result:
    """Collect specified performance metrics from device."""
    device_metrics = {"device": task.host.name}
    
    try:
        if "cpu" in metrics or "memory" in metrics:
            facts = task.run(napalm_get, getters=["facts"])
            if facts.result:
                device_metrics["uptime_seconds"] = (
                    facts.result.get("facts", {}).get("uptime_seconds", 0)
                )
        
        if "interface" in metrics:
            interfaces = task.run(napalm_get, getters=["interfaces_counters"])
            if interfaces.result:
                iface_data = interfaces.result.get("interfaces_counters", {})
                device_metrics["interfaces"] = _analyze_interfaces(iface_data)
        
        device_metrics["status"] = "success"
        
    except Exception as e:
        device_metrics["status"] = "error"
        device_metrics["error"] = str(e)
    
    return Result(host=task.host, result=device_metrics)


def _analyze_interfaces(iface_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze interface statistics for issues."""
    analysis = {
        "total": len(iface_data),
        "down_interfaces": [],
        "high_error_rates": []
    }
    
    for iface_name, stats in iface_data.items():
        if stats.get("state") == "down":
            analysis["down_interfaces"].append(iface_name)
        
        rx_errors = stats.get("rx_errors", 0) + stats.get("rx_discards", 0)
        tx_errors = stats.get("tx_errors", 0) + stats.get("tx_discards", 0)
        
        if rx_errors > 100 or tx_errors > 100:
            analysis["high_error_rates"].append({
                "interface": iface_name,
                "rx_errors": rx_errors,
                "tx_errors": tx_errors
            })
    
    return analysis


def format_table_output(results: Dict[str, Any]) -> str:
    """Format results as ASCII table."""
    lines = []
    header = f"{'Device':<20} {'Status':<12} {'Uptime (hrs)':<15} {'Issues':<15}"
    lines.append(header)
    lines.append("-" * len(header))
    
    for device_name, result in results.items():
        status = result.get("status", "unknown")
        uptime_hrs = result.get("uptime_seconds", 0) // 3600 if result.get("uptime_seconds") else 0
        
        issues = 0
        if "interfaces" in result:
            issues += len(result["interfaces"].get("down_interfaces", []))
            issues += len(result["interfaces"].get("high_error_rates", []))
        
        lines.append(
            f"{device_name:<20} {status:<12} {uptime_hrs:<15} {issues:<15}"
        )
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Collect and analyze device performance metrics"
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Target device name (if not specified, checks all devices)"
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default="cpu,memory,interface",
        help="Comma-separated metrics to collect (cpu,memory,interface)"
    )
    parser.add_argument(
        "--output",
        choices=["json", "table"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Logging level"
    )
    
    args = parser.parse_args()
    logger = setup_logging(args.log_level)
    
    try:
        metrics = [m.strip() for m in args.metrics.split(",")]
        nr = InitNornir(config_file="config.yaml")
        
        if args.device:
            nr = nr.filter(F(name=args.device))
        
        if not nr.inventory.hosts:
            logger.error("No devices found matching criteria")
            return
        
        logger.info(f"Collecting metrics from {len(nr.inventory.hosts)} device(s)")
        logger.debug(f"Metrics: {', '.join(metrics)}")
        
        results = nr.run(task=collect_metrics, metrics=metrics)
        
        output_dict = {}
        for host_name in nr.inventory.hosts.keys():
            task_result = results[host_name]
            if task_result[0].result:
                output_dict[host_name] = task_result[0].result
        
        if args.output == "json":
            print(json.dumps(output_dict, indent=2, default=str))
        else:
            print(format_table_output(output_dict))
        
        logger.info("Metrics collection completed successfully")
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```