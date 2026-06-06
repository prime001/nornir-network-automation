#!/usr/bin/env python
"""
Device Health Check Reporter.

Gathers device facts, interface status, and uptime to generate health reports.
Highlights devices with issues for operational awareness.

Usage: python device_health_check.py --inventory inventory.yaml --group switches
Prerequisites: nornir >= 3.0, napalm, SSH connectivity to devices.
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.networking import napalm_get


logger = logging.getLogger(__name__)


def check_device_health(task):
    """Gather device facts and interface status."""
    try:
        facts_result = task.run(napalm_get, getters=["facts"])
        iface_result = task.run(napalm_get, getters=["interfaces"])

        facts = facts_result[0].result.get("facts", {})
        interfaces = iface_result[0].result.get("interfaces", {})

        down_ifaces = sum(1 for v in interfaces.values()
                          if not v.get("is_up", False))

        issues = []
        if down_ifaces > 0:
            issues.append(f"{down_ifaces} interface(s) down")
        if facts.get("uptime_seconds", 0) < 3600:
            issues.append("Recently rebooted")

        return {
            "hostname": facts.get("hostname", "UNKNOWN"),
            "os_version": facts.get("os_version", "UNKNOWN"),
            "uptime_seconds": facts.get("uptime_seconds", 0),
            "serial": facts.get("serial_number", "UNKNOWN"),
            "vendor": facts.get("vendor", "UNKNOWN"),
            "issues": issues,
        }

    except Exception as e:
        logger.error(f"Health check failed for {task.host.name}: {e}")
        return {
            "hostname": task.host.name,
            "issues": [f"Error: {str(e)}"],
        }


def format_uptime(seconds):
    """Convert seconds to human-readable uptime."""
    if not seconds:
        return "UNKNOWN"
    days, r = divmod(int(seconds), 86400)
    hours, r = divmod(r, 3600)
    minutes, _ = divmod(r, 60)
    return f"{days}d {hours}h {minutes}m"


def generate_report(results, output_file=None):
    """Format and output health check report."""
    lines = [
        f"DEVICE HEALTH REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
    ]

    healthy, warnings = 0, 0

    for host_name, result in results.items():
        if result[0].failed:
            lines.append(f"{host_name}: FAILED - {result[0].exception}")
            warnings += 1
            continue

        health = result[0].result
        issues = health.get("issues", [])

        if issues:
            warnings += 1
            status = "⚠ WARNING"
        else:
            healthy += 1
            status = "✓ OK"

        uptime = format_uptime(health.get("uptime_seconds"))
        lines.extend([
            f"{health['hostname']} [{status}]",
            f"  Vendor: {health['vendor']} | OS: {health['os_version']}",
            f"  Uptime: {uptime} | Serial: {health['serial']}",
        ])

        if issues:
            lines.extend([f"  Issues: {', '.join(issues)}"])
        lines.append("")

    lines.extend([
        "=" * 70,
        f"Summary: {healthy} healthy, {warnings} with issues",
    ])

    report = "\n".join(lines)
    if output_file:
        Path(output_file).write_text(report)
        logger.info(f"Report saved to {output_file}")
    else:
        print(report)


def main():
    """Execute health check."""
    parser = argparse.ArgumentParser(
        description="Network Device Health Check Reporter"
    )
    parser.add_argument("--inventory", default="inventory.yaml",
                        help="Nornir inventory file")
    parser.add_argument("--group", help="Filter by group name")
    parser.add_argument("--device", help="Filter by device name pattern")
    parser.add_argument("--output", help="Save report to file")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
        if args.device:
            nr = nr.filter(F(name__contains=args.device))

        if not nr.inventory.hosts:
            logger.error("No devices matched filter")
            return 1

        logger.info(f"Health check on {len(nr.inventory.hosts)} device(s)")
        results = nr.run(task=check_device_health, num_workers=5)
        generate_report(results, args.output)

        return 0

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())