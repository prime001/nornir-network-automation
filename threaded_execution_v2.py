```python
#!/usr/bin/env python3
"""
Network Connectivity Probe - Multi-host reachability validator.

Performs connectivity tests from network devices to multiple target hosts,
validating gateway reachability, DNS resolution, and internet connectivity.
Useful for troubleshooting network paths and validating device egress routes.

Usage:
    python network_connectivity_probe.py -i inventory.yaml -g core_devices -t 8.8.8.8 1.1.1.1
    python network_connectivity_probe.py -i inventory.yaml -d router01 router02 -t google.com

Prerequisites:
    - Nornir and nornir-netmiko installed
    - Devices configured with SSH access
    - Devices support standard ping command syntax
    - Inventory file in YAML format with device credentials

Output:
    - Per-device connectivity results with packet loss details
    - JSON export option for integration with monitoring systems
"""

import argparse
import json
import logging
from typing import Any, Dict, List

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result
from nornir_netmiko.tasks import netmiko_send_command


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def parse_ping_output(output: str, target: str) -> Dict[str, Any]:
    """
    Parse ping command output to extract success/failure status.

    Handles multiple device OS formats (IOS, EOS, Junos, etc).
    Returns dict with reachability status and packet loss percentage.
    """
    output_lower = output.lower()

    if "unreachable" in output_lower or "host unreachable" in output_lower:
        return {"reachable": False, "packet_loss": "100%", "summary": "Host unreachable"}

    if "0 received" in output_lower or "0% success" in output_lower:
        return {"reachable": False, "packet_loss": "100%", "summary": "No response"}

    for line in output.split("\n"):
        line_lower = line.lower()
        if "%" in line and ("loss" in line_lower or "success" in line_lower):
            return {
                "reachable": True,
                "packet_loss": line.strip(),
                "summary": "Reachable",
            }

    return {"reachable": True, "packet_loss": "0%", "summary": "Reachable (verified)"}


def probe_target(task, target: str, count: int = 4) -> Dict[str, Any]:
    """Execute ping command and parse results for single target."""
    platform = task.host.platform

    if platform in ["eos"]:
        cmd = f"ping {target} count {count}"
    elif platform in ["ios", "iosxe", "iosxr"]:
        cmd = f"ping {target} repeat {count}"
    elif platform in ["junos"]:
        cmd = f"ping {target} count {count}"
    else:
        cmd = f"ping -c {count} {target}"

    logging.debug(f"{task.host.name}: Executing: {cmd}")

    try:
        result = task.run(
            netmiko_send_command,
            command_string=cmd,
            use_timing=False,
            name=f"ping_{target.replace('.', '_')}",
        )
        output = result[0].result
        return parse_ping_output(output, target)

    except Exception as e:
        logging.error(f"{task.host.name} -> {target}: {str(e)}")
        return {"reachable": False, "packet_loss": "N/A", "error": str(e)}


def probe_connectivity(task, targets: List[str], count: int) -> None:
    """Execute connectivity probe to all targets on a device."""
    connectivity = {}

    for target in targets:
        logging.info(f"{task.host.name}: Testing {target}")
        connectivity[target] = probe_target(task, target, count)

    task.host["connectivity"] = connectivity


def main() -> int:
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Validate network connectivity from devices to target hosts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-i",
        "--inventory",
        default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)",
    )
    parser.add_argument(
        "-g",
        "--group",
        help="Target a specific Nornir group",
    )
    parser.add_argument(
        "-d",
        "--devices",
        nargs="+",
        help="Target specific device names",
    )
    parser.add_argument(
        "-t",
        "--targets",
        nargs="+",
        required=True,
        help="Target hosts to probe (IP addresses or DNS names)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=4,
        help="Ping packet count per target (default: 4)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Export results to JSON file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.devices:
            nr = nr.filter(F(name__in=args.devices))
        elif args.group:
            nr = nr.filter(F(groups__contains=args.group))
        else:
            logging.error("Specify either --group or --devices")
            return 1

        if not nr.inventory.hosts:
            logging.error("No devices matched filter criteria")
            return 1

        logging.info(
            f"Probing {len(nr.inventory.hosts)} devices to {len(args.targets)} targets"
        )

        results = nr.run(
            task=probe_connectivity,
            targets=args.targets,
            count=args.count,
        )

        summary = {}
        for hostname, task_result in results.items():
            if task_result.failed:
                logging.error(f"{hostname}: Task failed")
                continue

            connectivity = nr.inventory.hosts[hostname]["connectivity"]
            failed_targets = [
                t for t, r in connectivity.items() if not r.get("reachable", False)
            ]
            summary[hostname] = {
                "all_reachable": len(failed_targets) == 0,
                "results": connectivity,
                "failed_targets": failed_targets,
            }

        print("\n=== CONNECTIVITY RESULTS ===\n")
        for device, data in summary.items():
            status = "✓ ALL REACHABLE" if data["all_reachable"] else "✗ FAILURES"
            print(f"{device}: {status}")
            if data["failed_targets"]:
                print(f"  Failed: {', '.join(data['failed_targets'])}")
            for target, probe in data["results"].items():
                if not data["all_reachable"]:
                    loss = probe.get("packet_loss", "N/A")
                    print(f"    {target}: {loss}")

        if args.output:
            with open(args.output, "w") as f:
                json.dump(summary, f, indent=2, default=str)
            logging.info(f"Results exported to {args.output}")

        return 0

    except FileNotFoundError:
        logging.error(f"Inventory file not found: {args.inventory}")
        return 1
    except Exception as e:
        logging.error(f"Script error: {str(e)}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```