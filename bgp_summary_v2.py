```python
"""
Network device health monitoring using Nornir.

Purpose:
    Connects to network devices, gathers health metrics (uptime, OS version,
    interface status), and generates a health report with problem identification.

Usage:
    python device_health.py --inventory hosts.yaml --username admin --password pass
    python device_health.py -i hosts.yaml -u admin -p pass --device-filter core*

Prerequisites:
    - Nornir installed with nornir_netmiko/nornir_napalm
    - Inventory file in YAML format with device definitions
    - Device credentials with read access
    - Supported platforms: ios, eos, junos, nxos

Inventory format (hosts.yaml):
    ---
    all:
      children:
        routers:
          hosts:
            router1:
              hostname: 192.168.1.1
              username: admin
              password: secret
              platform: ios
"""

import logging
import argparse
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir.plugins.tasks.networking import napalm_get
from nornir.core.filter import F

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_device_health(task: Task) -> Result:
    """Gather device health metrics using NAPALM."""
    health_data = {
        "name": task.host.name,
        "reachable": False,
        "facts": {},
        "problem_interfaces": [],
        "errors": []
    }

    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        health_data["facts"] = facts_result[0].result.get("facts", {})

        interfaces_result = task.run(napalm_get, getters=["interfaces"])
        all_interfaces = interfaces_result[0].result.get("interfaces", {})

        for iface_name, iface_data in all_interfaces.items():
            if not iface_data.get("is_up"):
                health_data["problem_interfaces"].append(iface_name)

        health_data["reachable"] = True

    except Exception as e:
        health_data["errors"].append(str(e))
        logger.warning(f"Error querying {task.host.name}: {e}")

    return Result(host=task.host, result=health_data)


def format_uptime(seconds):
    """Convert uptime seconds to human-readable format."""
    if not seconds:
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


def main():
    parser = argparse.ArgumentParser(
        description="Monitor network device health and generate report"
    )
    parser.add_argument(
        "-i", "--inventory",
        default="hosts.yaml",
        help="Path to Nornir inventory file"
    )
    parser.add_argument(
        "-u", "--username",
        required=True,
        help="Username for device authentication"
    )
    parser.add_argument(
        "-p", "--password",
        required=True,
        help="Password for device authentication"
    )
    parser.add_argument(
        "-f", "--device-filter",
        help="Filter devices by name pattern"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.device_filter:
            nr = nr.filter(F(name__contains=args.device_filter))

        logger.info(f"Starting health check on {len(nr.inventory.hosts)} device(s)")

        results = nr.run(task=check_device_health)

        print("\n" + "=" * 80)
        print("NETWORK DEVICE HEALTH REPORT")
        print("=" * 80)

        healthy_count = 0
        for host_name in sorted(results.keys()):
            multi_result = results[host_name]
            health = multi_result[0].result

            if health["reachable"]:
                healthy_count += 1
                facts = health["facts"]
                has_issues = len(health["problem_interfaces"]) > 0
                status = "⚠ ISSUES" if has_issues else "✓ HEALTHY"

                print(f"\n[{status}] {host_name}")
                print(f"  OS Version: {facts.get('os_version', 'N/A')}")
                print(f"  Uptime:     {format_uptime(facts.get('uptime_seconds'))}")
                print(f"  Vendor:     {facts.get('vendor', 'N/A')}")

                if health["problem_interfaces"]:
                    print(f"  Down Interfaces: {', '.join(health['problem_interfaces'])}")
            else:
                print(f"\n[✗ UNREACHABLE] {host_name}")
                for error in health["errors"]:
                    print(f"  Error: {error}")

        print("\n" + "=" * 80)
        print(f"Summary: {healthy_count}/{len(nr.inventory.hosts)} devices reachable")
        print("=" * 80 + "\n")

    except FileNotFoundError as e:
        logger.error(f"Inventory file not found: {args.inventory}")
        raise
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        raise


if __name__ == "__main__":
    main()
```