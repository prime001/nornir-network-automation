Device Health Monitor - Nornir Network Automation Script

Purpose:
    Monitors network device system health metrics (CPU, memory, uptime, disk) and
    generates a health status report with configurable thresholds. Useful for
    identifying devices approaching resource exhaustion before operational impact.

Usage:
    python device_health_monitor.py --devices all --username admin --password secret
    python device_health_monitor.py --devices core_routers --cpu-threshold 80 --mem-threshold 85

Prerequisites:
    - Nornir installed with NAPALM plugin
    - Device inventory configured (hosts.yaml)
    - Network devices reachable via NAPALM-supported transport (SSH, netmiko)
    - User credentials with read-only device access
    - Device support for facts/getEnvironment methods

Author: Network Engineering Team
"""

import argparse
import logging
from collections import defaultdict
from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get
from nornir.plugins.functions.text import print_result


def setup_logging(verbose=False):
    """Configure logging with appropriate verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=level,
    )
    return logging.getLogger(__name__)


def get_health_status(device_name, facts, environment, thresholds):
    """
    Evaluate device health and return status dict.

    Args:
        device_name: Device hostname
        facts: Device facts dict from NAPALM get_facts()
        environment: Device environment dict from NAPALM get_environment()
        thresholds: Dict with 'cpu', 'memory', 'disk' threshold percentages

    Returns:
        Dict with health status and unhealthy metrics
    """
    health_status = {
        "device": device_name,
        "status": "healthy",
        "issues": [],
        "metrics": {},
    }

    try:
        uptime_seconds = facts.get("uptime", 0)
        uptime_days = uptime_seconds // 86400
        health_status["metrics"]["uptime_days"] = uptime_days

        if "cpu" in environment:
            for cpu_entry in environment.get("cpu", {}).values():
                cpu_percent = cpu_entry.get("%usage", 0)
                health_status["metrics"]["cpu_percent"] = cpu_percent
                if cpu_percent > thresholds["cpu"]:
                    health_status["status"] = "warning"
                    health_status["issues"].append(
                        f"CPU at {cpu_percent}% (threshold: {thresholds['cpu']}%)"
                    )

        if "memory" in environment:
            mem_data = environment["memory"].get("System memory", {})
            if mem_data:
                mem_used = mem_data.get("used_ram", 0)
                mem_total = mem_data.get("available_ram", 1)
                mem_percent = int((mem_used / mem_total) * 100) if mem_total > 0 else 0
                health_status["metrics"]["memory_percent"] = mem_percent
                if mem_percent > thresholds["memory"]:
                    health_status["status"] = "warning"
                    health_status["issues"].append(
                        f"Memory at {mem_percent}% (threshold: {thresholds['memory']}%)"
                    )

        return health_status

    except (KeyError, TypeError, ZeroDivisionError) as e:
        logging.warning(f"{device_name}: Error parsing health metrics - {e}")
        health_status["status"] = "unknown"
        health_status["issues"].append("Failed to parse device metrics")
        return health_status


def gather_device_health(host, thresholds):
    """Gather health metrics from a device."""
    device_name = host.name

    try:
        facts_result = host.run_task(
            napalm_get,
            getters=["facts", "environment"],
            timeout=host.timeout or 30,
        )

        facts = facts_result[0].result.get("facts", {})
        environment = facts_result[0].result.get("environment", {})

        health = get_health_status(device_name, facts, environment, thresholds)
        return health

    except Exception as e:
        logging.error(f"{device_name}: Connection failed - {e}")
        return {
            "device": device_name,
            "status": "critical",
            "issues": [f"Unable to connect: {str(e)}"],
            "metrics": {},
        }


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Monitor network device health metrics"
    )
    parser.add_argument(
        "--inventory",
        default="inventory",
        help="Path to Nornir inventory directory (default: inventory)",
    )
    parser.add_argument(
        "--devices",
        default="all",
        help="Device filter: 'all' or group/name (default: all)",
    )
    parser.add_argument(
        "--username", help="Device username (overrides inventory)"
    )
    parser.add_argument(
        "--password", help="Device password (overrides inventory)"
    )
    parser.add_argument(
        "--cpu-threshold",
        type=int,
        default=80,
        help="CPU usage warning threshold %% (default: 80)",
    )
    parser.add_argument(
        "--mem-threshold",
        type=int,
        default=85,
        help="Memory usage warning threshold %% (default: 85)",
    )
    parser.add_argument(
        "--disk-threshold",
        type=int,
        default=90,
        help="Disk usage warning threshold %% (default: 90)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Device connection timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    args = parser.parse_args()
    logger = setup_logging(args.verbose)

    logger.info("Initializing Nornir inventory")
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return 1

    if args.devices != "all":
        nr = nr.filter(F(name__contains=args.devices) | F(groups__contains=args.devices))

    if not nr.inventory.hosts:
        logger.error("No devices matched filter")
        return 1

    if args.username:
        for host in nr.inventory.hosts.values():
            host.username = args.username
    if args.password:
        for host in nr.inventory.hosts.values():
            host.password = args.password
    if args.timeout:
        for host in nr.inventory.hosts.values():
            host.timeout = args.timeout

    thresholds = {
        "cpu": args.cpu_threshold,
        "memory": args.mem_threshold,
        "disk": args.disk_threshold,
    }

    logger.info(f"Monitoring {len(nr.inventory.hosts)} devices")
    logger.info(f"Thresholds: CPU={thresholds['cpu']}%, Memory={thresholds['memory']}%")

    results = defaultdict(list)

    for device_name, host in nr.inventory.hosts.items():
        health = gather_device_health(host, thresholds)
        status = health["status"]
        results[status].append(health)

        status_symbol = "✓" if status == "healthy" else "⚠" if status == "warning" else "✗"
        logger.info(f"{status_symbol} {device_name}: {status.upper()}")

        if health["issues"]:
            for issue in health["issues"]:
                logger.warning(f"  → {issue}")

    print("\n" + "="*70)
    print("DEVICE HEALTH MONITOR REPORT")
    print("="*70)

    for status in ["healthy", "warning", "critical", "unknown"]:
        devices = results.get(status, [])
        if devices:
            print(f"\n{status.upper()} ({len(devices)} devices):")
            for device_health in devices:
                metrics_str = " | ".join(
                    [f"{k}={v}" for k, v in device_health["metrics"].items()]
                )
                print(f"  {device_health['device']}: {metrics_str}")
                for issue in device_health["issues"]:
                    print(f"    ⚠ {issue}")

    healthy_count = len(results.get("healthy", []))
    total_count = sum(len(v) for v in results.values())
    print(f"\nSummary: {healthy_count}/{total_count} devices healthy")
    print("="*70)

    return 0 if len(results.get("critical", [])) == 0 else 1


if __name__ == "__main__":
    exit(main())