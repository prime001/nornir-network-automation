```python
#!/usr/bin/env python3
"""
Network Device Health Monitor - Gathers and reports health metrics from network devices.

Purpose: Monitor CPU, memory, uptime, and disk usage across network devices. Useful for
capacity planning, troubleshooting, and anomaly detection.

Usage:
    python device_health_monitor.py --inventory inventory.yaml --device-group core
    python device_health_monitor.py --device 192.168.1.1 --username admin
    python device_health_monitor.py --inventory inventory.yaml --warn-cpu 75 --warn-mem 80

Prerequisites:
    - nornir[netmiko,napalm] installed
    - inventory.yaml with device definitions
    - Network device credentials via --username/--password or SSH keys
    - Devices support NAPALM facts and environment getters
"""

import argparse
import logging
import sys
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.tasks.networking import napalm_get
from nornir.core.filter import F

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )


def get_device_health(task: Task) -> Result:
    """Gather health metrics from device using NAPALM getters."""
    try:
        result = task.run(napalm_get, getters=['facts', 'environment'])
        facts = result[0].result.get('facts', {})
        environment = result[0].result.get('environment', {})
        
        metrics = {
            'uptime': facts.get('uptime_seconds', 'N/A'),
            'hostname': facts.get('hostname', 'N/A'),
            'os_version': facts.get('os_version', 'N/A'),
        }
        
        cpu_data = environment.get('cpu', {})
        mem_data = environment.get('memory', {})
        
        if isinstance(cpu_data, dict):
            metrics['cpu_load'] = cpu_data.get('cpu%', 'N/A')
        if isinstance(mem_data, dict):
            metrics['memory'] = mem_data.get('usage', 'N/A')
        
        return Result(host=task.host, result=metrics)
    
    except Exception as e:
        logger.error(f"Failed to retrieve health metrics for {task.host.name}: {e}")
        return Result(host=task.host, result={'error': str(e)}, failed=True)


def check_health_thresholds(task: Task, warn_cpu: int, warn_mem: int) -> Result:
    """Check device health metrics against warning thresholds."""
    health = task.run(get_device_health)
    
    if health[0].failed:
        return health[0]
    
    metrics = health[0].result
    warnings = []
    
    cpu = metrics.get('cpu_load')
    if isinstance(cpu, (int, float)) and cpu > warn_cpu:
        warnings.append(f"High CPU: {cpu}%")
    
    mem = metrics.get('memory')
    if isinstance(mem, (int, float)) and mem > warn_mem:
        warnings.append(f"High Memory: {mem}%")
    
    metrics['warnings'] = warnings
    return Result(host=task.host, result=metrics)


def format_uptime(seconds: int) -> str:
    """Convert uptime in seconds to human-readable format."""
    if not isinstance(seconds, int):
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def print_health_report(results: dict) -> None:
    """Print formatted health status report."""
    print("\n" + "="*85)
    print(f"{'Device':<20} {'Status':<10} {'Uptime':<15} {'CPU':<8} {'Memory':<8}")
    print("="*85)
    
    for device_name, result in results.items():
        if result.failed:
            error_msg = result.result.get('error', 'Unknown error')
            print(f"{device_name:<20} {'FAILED':<10} {error_msg}")
            continue
        
        metrics = result.result
        uptime = format_uptime(metrics.get('uptime'))
        cpu = f"{metrics.get('cpu_load')}%" if isinstance(metrics.get('cpu_load'), (int, float)) else "N/A"
        mem = f"{metrics.get('memory')}%" if isinstance(metrics.get('memory'), (int, float)) else "N/A"
        status = "WARNING" if metrics.get('warnings') else "OK"
        
        print(f"{device_name:<20} {status:<10} {uptime:<15} {cpu:<8} {mem:<8}")
        for warning in metrics.get('warnings', []):
            print(f"  └─ {warning}")
    
    print("="*85 + "\n")


def main() -> int:
    """Main entry point for device health monitoring."""
    parser = argparse.ArgumentParser(
        description='Monitor network device health metrics (CPU, memory, uptime)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--inventory', default='inventory.yaml',
                        help='Path to nornir inventory file (default: inventory.yaml)')
    parser.add_argument('--device', help='Monitor specific device by hostname or IP')
    parser.add_argument('--device-group', help='Filter devices by inventory group')
    parser.add_argument('--username', help='SSH username for device authentication')
    parser.add_argument('--password', help='SSH password for device authentication')
    parser.add_argument('--warn-cpu', type=int, default=80,
                        help='CPU usage warning threshold in percent (default: 80)')
    parser.add_argument('--warn-mem', type=int, default=85,
                        help='Memory usage warning threshold in percent (default: 85)')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Logging verbosity level (default: INFO)')
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.username:
            nr.inventory.defaults.username = args.username
        if args.password:
            nr.inventory.defaults.password = args.password
        
        if args.device:
            nr = nr.filter(name=args.device)
        elif args.device_group:
            nr = nr.filter(F(groups__contains=args.device_group))
        
        if not nr.inventory.hosts:
            logger.error("No devices matched the specified filter criteria")
            return 1
        
        logger.info(f"Starting health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(
            task=check_health_thresholds,
            warn_cpu=args.warn_cpu,
            warn_mem=args.warn_mem
        )
        
        print_health_report(dict(results))
        
        failed_devices = sum(1 for r in results.values() if r.failed)
        if failed_devices > 0:
            logger.warning(f"{failed_devices} device(s) failed to report health metrics")
            return 1
        
        return 0
    
    except FileNotFoundError:
        logger.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error during execution: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```