```python
"""
Interface Health and Error Analysis Script

Collects interface statistics from network devices and generates a health report,
identifying interfaces with high error rates or problematic conditions.

Usage:
    python interface_health.py --inventory inventory.yml --username admin --password pass
    python interface_health.py --inventory inventory.yml --username admin --password pass --device router1
    python interface_health.py --inventory inventory.yml --username admin --password pass --threshold-errors 100

Prerequisites:
    - nornir, napalm installed
    - Network device inventory in YAML format
    - SSH/NETCONF connectivity to devices
    - Devices support NAPALM get_interfaces getter
"""

import argparse
import logging
import sys

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir.tasks.networking import napalm_get


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def collect_interface_stats(task: Task) -> Result:
    """Collect interface statistics from device using NAPALM."""
    try:
        result = task.run(napalm_get, getters=["interfaces", "interfaces_counters"])
        return result
    except Exception as e:
        logger.error(f"Failed to collect stats from {task.host.name}: {e}")
        return Result(host=task.host, failed=True, exception=e)


def analyze_interface_health(stats, error_threshold=10, drop_threshold=50):
    """Analyze interface health and return problems and health score."""
    problems = []
    interfaces = stats.get("interfaces", {})
    counters = stats.get("interfaces_counters", {}).get("interfaces", {})
    
    healthy_count = 0
    for if_name, if_data in interfaces.items():
        is_healthy = True
        issues = []
        
        if not if_data.get("is_up"):
            is_healthy = False
            issues.append("Down")
        
        if not if_data.get("is_enabled"):
            is_healthy = False
            issues.append("Disabled")
        
        if if_name in counters:
            counter = counters[if_name]
            rx_err = counter.get("rx_errors", 0)
            tx_err = counter.get("tx_errors", 0)
            rx_drop = counter.get("rx_discards", 0)
            tx_drop = counter.get("tx_discards", 0)
            
            if rx_err > error_threshold or tx_err > error_threshold:
                is_healthy = False
                issues.append(f"High errors (RX:{rx_err}, TX:{tx_err})")
            
            if rx_drop > drop_threshold or tx_drop > drop_threshold:
                is_healthy = False
                issues.append(f"High drops (RX:{rx_drop}, TX:{tx_drop})")
        
        if is_healthy:
            healthy_count += 1
        else:
            problems.append({"name": if_name, "issues": issues})
    
    total = len(interfaces)
    score = (healthy_count / total * 100) if total > 0 else 0
    return problems, score


def generate_report(results, error_threshold, drop_threshold):
    """Generate and display health report."""
    print("\n" + "=" * 80)
    print("INTERFACE HEALTH REPORT")
    print("=" * 80)
    
    total_devices = 0
    total_problems = 0
    
    for host_name, task_result in results.items():
        if task_result.failed:
            print(f"\n{host_name}: FAILED")
            continue
        
        total_devices += 1
        stats = task_result[0].result
        problems, score = analyze_interface_health(
            stats, error_threshold, drop_threshold
        )
        
        status = "✓" if score >= 90 else "⚠" if score >= 70 else "✗"
        print(f"\n{host_name} {status}")
        print(f"  Health Score: {score:.1f}%")
        
        if problems:
            total_problems += len(problems)
            print(f"  Problem Interfaces ({len(problems)}):")
            for problem in problems:
                print(f"    - {problem['name']}")
                for issue in problem["issues"]:
                    print(f"      • {issue}")
    
    print("\n" + "=" * 80)
    print(f"Summary: {total_devices} devices, {total_problems} problem interfaces")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze network device interface health and error rates"
    )
    parser.add_argument("--inventory", required=True, help="Path to inventory file")
    parser.add_argument("--username", required=True, help="Device username")
    parser.add_argument("--password", required=True, help="Device password")
    parser.add_argument("--device", help="Specific device to analyze (optional)")
    parser.add_argument(
        "--threshold-errors",
        type=int,
        default=10,
        help="Error count threshold (default: 10)"
    )
    parser.add_argument(
        "--threshold-drops",
        type=int,
        default=50,
        help="Drop count threshold (default: 50)"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.device:
            nr = nr.filter(F(name=args.device))
            if not nr.inventory.hosts:
                logger.error(f"Device '{args.device}' not found in inventory")
                sys.exit(1)
        
        for host in nr.inventory.hosts.values():
            host.username = args.username
            host.password = args.password
        
        logger.info(f"Analyzing {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=collect_interface_stats)
        generate_report(results, args.threshold_errors, args.threshold_drops)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```