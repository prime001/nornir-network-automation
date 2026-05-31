```python
"""
Device Health Check Script

Provides comprehensive health monitoring for network devices using Nornir and NAPALM.
Collects CPU, memory, uptime, and temperature metrics from devices in parallel.

Usage:
    python device_health_check.py --inventory hosts.yaml --groups router
    python device_health_check.py --inventory hosts.yaml --warn-cpu 80
    python device_health_check.py --inventory hosts.yaml --devices router1,router2

Prerequisites:
    - Nornir with NAPALM plugin installed
    - Network devices reachable via SSH/NETCONF
    - Device credentials configured in inventory
    - NAPALM drivers available for device platforms

Example inventory (hosts.yaml):
    devices:
      router1:
        host: 10.0.0.1
        username: admin
        password: pass123
        platform: ios
      router2:
        host: 10.0.0.2
        username: admin
        password: pass123
        platform: junos
"""

import argparse
import logging
import sys

from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir.core.filter import F
from nornir_napalm.plugins.tasks import napalm_get


logger = logging.getLogger(__name__)


def gather_health(task: Task, warn_cpu: int, warn_mem: int) -> Result:
    """Collect device health metrics from NAPALM environment getter."""
    try:
        env_r = task.run(napalm_get, getters=['environment'])
        facts_r = task.run(napalm_get, getters=['facts'])
        
        env_get = env_r[task.host.name]
        facts_get = facts_r[task.host.name]
        
        env = env_get[0].result.get('environment', {})
        facts = facts_get[0].result.get('facts', {})
        
        result = {
            'hostname': task.host.name,
            'platform': task.host.platform,
            'cpu_percent': None,
            'memory_percent': None,
            'temperatures': [],
            'uptime_seconds': None,
            'alerts': []
        }
        
        cpu_data = env.get('cpu', {})
        if '%usage' in cpu_data:
            cpu_pct = cpu_data['%usage']
            result['cpu_percent'] = cpu_pct
            if cpu_pct > warn_cpu:
                result['alerts'].append(f"High CPU: {cpu_pct}%")
        
        memory_data = env.get('memory', {})
        if memory_data:
            used = memory_data.get('used_ram', 0)
            available = memory_data.get('available_ram', 1)
            if used + available > 0:
                mem_pct = round((used / (used + available)) * 100, 1)
                result['memory_percent'] = mem_pct
                if mem_pct > warn_mem:
                    result['alerts'].append(f"High Memory: {mem_pct}%")
        
        for sensor_name, sensor_data in env.get('temperature', {}).items():
            if 'current_temperature' in sensor_data:
                result['temperatures'].append({
                    'sensor': sensor_name,
                    'celsius': sensor_data['current_temperature']
                })
        
        if 'uptime_seconds' in facts:
            result['uptime_seconds'] = facts['uptime_seconds']
        
        return Result(host=task.host, result=result)
        
    except Exception as e:
        logger.error(f"{task.host.name}: {e}")
        return Result(
            host=task.host,
            result={'hostname': task.host.name, 'error': str(e)},
            failed=True
        )


def display_report(results):
    """Print formatted health report to console."""
    print("\n" + "=" * 80)
    print("DEVICE HEALTH REPORT".center(80))
    print("=" * 80)
    
    for host, multi_result in results.items():
        task_result = multi_result[0]
        
        if task_result.failed:
            res = task_result.result
            print(f"\n[ERROR] {res['hostname']}: {res['error']}")
            continue
        
        res = task_result.result
        print(f"\n{res['hostname']} ({res['platform']})")
        
        if res['cpu_percent'] is not None:
            icon = "⚠️" if any('CPU' in a for a in res['alerts']) else "✓"
            print(f"  {icon} CPU: {res['cpu_percent']}%")
        
        if res['memory_percent'] is not None:
            icon = "⚠️" if any('Memory' in a for a in res['alerts']) else "✓"
            print(f"  {icon} Memory: {res['memory_percent']}%")
        
        for temp in res['temperatures']:
            print(f"  🌡️  {temp['sensor']}: {temp['celsius']}°C")
        
        if res['uptime_seconds']:
            days = res['uptime_seconds'] // 86400
            hours = (res['uptime_seconds'] % 86400) // 3600
            print(f"  ↑ Uptime: {days}d {hours}h")
        
        for alert in res['alerts']:
            print(f"  ⚠️  {alert}")
    
    print("\n" + "=" * 80 + "\n")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    
    parser = argparse.ArgumentParser(
        description='Monitor network device health metrics',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Example: python device_health_check.py --inventory hosts.yaml --groups core'
    )
    parser.add_argument('--inventory', required=True, help='Path to Nornir inventory file')
    parser.add_argument('--groups', help='Filter by groups (comma-separated)')
    parser.add_argument('--devices', help='Filter by device names (comma-separated)')
    parser.add_argument('--warn-cpu', type=int, default=85, help='CPU threshold %% (default: 85)')
    parser.add_argument('--warn-memory', type=int, default=90, help='Memory threshold %% (default: 90)')
    parser.add_argument('--output', help='Write report to file (default: stdout)')
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=args.inventory)
        
        if args.groups:
            group_list = [g.strip() for g in args.groups.split(',')]
            nr = nr.filter(F(groups__any=group_list))
            logger.info(f"Filtered to groups: {group_list}")
        
        if args.devices:
            device_list = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(F(name__any=device_list))
            logger.info(f"Filtered to devices: {device_list}")
        
        logger.info(f"Collecting health metrics from {len(nr.inventory.hosts)} devices...")
        
        results = nr.run(
            task=gather_health,
            warn_cpu=args.warn_cpu,
            warn_mem=args.warn_memory
        )
        
        if args.output:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                display_report(results)
            with open(args.output, 'w') as f:
                f.write(buf.getvalue())
            logger.info(f"Report saved to {args.output}")
        else:
            display_report(results)
        
        logger.info("Health check completed successfully")
        
    except KeyboardInterrupt:
        print("\n[interrupted by user]")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```