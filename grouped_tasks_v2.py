```python
"""
Interface Statistics Analyzer - Device Network Performance Monitor

Purpose:
    Gathers and analyzes interface statistics from network devices to identify
    problematic interfaces with high error rates, drops, or other anomalies.
    Useful for capacity planning, troubleshooting, and network health monitoring.

Usage:
    python interface_stats_analyzer.py --devices all
    python interface_stats_analyzer.py --devices router1,router2
    python interface_stats_analyzer.py --threshold-errors 100
    python interface_stats_analyzer.py --output json

Prerequisites:
    - nornir installed (pip install nornir)
    - nornir inventory file configured
    - Network devices supporting NAPALM get_interfaces_counters()
    - Credentials configured in environment or nornir config
    - paramiko, netmiko, and napalm installed

Author: Network Engineer
"""

import logging
import argparse
import json
from typing import Dict, List, Any
from nornir import InitNornir
from nornir.core.task import Result
from nornir_napalm.plugins.tasks import napalm_get
from nornir.core.filter import F


logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def gather_interface_stats(task) -> Result:
    """Gather interface statistics from device."""
    try:
        result = task.run(napalm_get, getters=['interfaces_counters'])
        return result
    except Exception as e:
        logger.error(f"{task.host.name}: Failed to gather stats - {e}")
        return Result(host=task.host, failed=True, exception=e)


def analyze_interfaces(stats: Dict[str, Any],
                      error_threshold: int = 100,
                      drop_threshold: int = 50) -> Dict[str, List[str]]:
    """Analyze interface stats and flag problematic interfaces."""
    problems = {"errors": [], "drops": [], "disabled": []}

    for iface, counters in stats.items():
        total_errors = (counters.get("rx_errors", 0) +
                       counters.get("tx_errors", 0) +
                       counters.get("rx_crc_errors", 0))
        total_drops = (counters.get("rx_discards", 0) +
                      counters.get("tx_discards", 0))

        if total_errors >= error_threshold:
            problems["errors"].append(
                f"{iface}: {total_errors} errors"
            )
        if total_drops >= drop_threshold:
            problems["drops"].append(
                f"{iface}: {total_drops} drops"
            )
        if not counters.get("is_up", True):
            problems["disabled"].append(iface)

    return problems


def format_report(results: Dict, output_format: str = "text") -> str:
    """Format analysis results for output."""
    if output_format == "json":
        return json.dumps(results, indent=2)

    report = "\n=== Interface Statistics Analysis Report ===\n"
    for device, data in results.items():
        if data["failed"]:
            report += f"\n{device}: FAILED - {data['error']}\n"
        else:
            problems = data["problems"]
            report += f"\n{device}:\n"
            if not any(problems.values()):
                report += "  Status: OK - No issues detected\n"
            else:
                if problems["errors"]:
                    report += f"  Errors ({len(problems['errors'])}): "
                    report += ", ".join(problems["errors"][:3])
                    report += "\n"
                if problems["drops"]:
                    report += f"  Drops ({len(problems['drops'])}): "
                    report += ", ".join(problems["drops"][:3])
                    report += "\n"
                if problems["disabled"]:
                    report += f"  Disabled: {', '.join(problems['disabled'][:5])}\n"

    return report


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--devices",
        default="all",
        help="Comma-separated list of devices or 'all' (default: all)"
    )
    parser.add_argument(
        "--threshold-errors",
        type=int,
        default=100,
        help="Error count threshold (default: 100)"
    )
    parser.add_argument(
        "--threshold-drops",
        type=int,
        default=50,
        help="Drop count threshold (default: 50)"
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        nr = InitNornir(config_file="config.yaml")

        if args.devices != "all":
            devices = args.devices.split(",")
            nr = nr.filter(F(name__in=devices))

        logger.info(f"Gathering stats from {len(nr.inventory.hosts)} devices")
        results = nr.run(task=gather_interface_stats)

        analysis = {}
        for device_name, task_result in results.items():
            if task_result.failed:
                analysis[device_name] = {
                    "failed": True,
                    "error": str(task_result.exception)
                }
            else:
                stats = task_result[0].result.get("interfaces_counters", {})
                problems = analyze_interfaces(
                    stats,
                    error_threshold=args.threshold_errors,
                    drop_threshold=args.threshold_drops
                )
                analysis[device_name] = {
                    "failed": False,
                    "problems": problems,
                    "interface_count": len(stats)
                }

        report = format_report(analysis, args.output)
        print(report)

        logger.info("Analysis complete")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
```