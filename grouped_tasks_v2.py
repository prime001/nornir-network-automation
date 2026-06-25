```python
"""
Interface Error Statistics and Health Report

Collects and reports on interface error counters (CRC errors, input/output
errors, discards) from network devices to identify potential connectivity
issues and hardware problems.

Usage:
    python interface_errors.py --inventory inventory.yaml --devices all
    python interface_errors.py --inventory inv.yaml --devices router1,router2

Prerequisites:
    - Nornir installed with netmiko plugin
    - SSH connectivity to all target devices
    - Device support: Cisco IOS/IOS-XE/NXOS

Examples:
    Check interfaces with >5 errors: --error-threshold 5
    Check specific interfaces: --interface-filter "Gig.*"
    Save output: --output errors_report.txt
"""

import logging
import argparse
import re
from dataclasses import dataclass
from typing import Dict
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir.core.filter import F

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class InterfaceStats:
    """Store interface error statistics."""
    name: str
    status: str
    crc_errors: int = 0
    input_errors: int = 0
    output_errors: int = 0
    discards: int = 0

    @property
    def total_errors(self) -> int:
        return self.crc_errors + self.input_errors + self.output_errors + self.discards

    def is_problematic(self, threshold: int) -> bool:
        return self.total_errors >= threshold


def parse_cisco_interfaces(output: str) -> Dict[str, InterfaceStats]:
    """Parse Cisco 'show interfaces' output for error counters."""
    interfaces = {}
    current_if = None

    for line in output.split('\n'):
        match = re.match(r'^(\S+)\s+is\s+(\w+)', line)
        if match:
            current_if = match.group(1)
            status = match.group(2).lower()
            interfaces[current_if] = InterfaceStats(name=current_if, status=status)
            continue

        if not current_if:
            continue

        if 'CRC' in line:
            num = re.search(r'(\d+)', line)
            if num:
                interfaces[current_if].crc_errors = int(num.group(1))
        elif 'input error' in line.lower():
            num = re.search(r'(\d+)', line)
            if num:
                interfaces[current_if].input_errors = int(num.group(1))
        elif 'output error' in line.lower():
            num = re.search(r'(\d+)', line)
            if num:
                interfaces[current_if].output_errors = int(num.group(1))
        elif 'discards' in line.lower():
            num = re.search(r'(\d+)', line)
            if num:
                interfaces[current_if].discards = int(num.group(1))

    return interfaces


def collect_interface_errors(task: Task, threshold: int, if_filter: str) -> Result:
    """Gather interface error statistics from target device."""
    try:
        r = task.run(netmiko_send_command, command_string="show interfaces")
        output = r[0].result

        if 'cisco' in task.host.platform.lower():
            interfaces = parse_cisco_interfaces(output)
        else:
            logger.warning(f"Platform {task.host.platform} not fully supported")
            interfaces = {}

        if if_filter:
            interfaces = {
                k: v for k, v in interfaces.items()
                if re.search(if_filter, k)
            }

        problems = {
            name: iface for name, iface in interfaces.items()
            if iface.is_problematic(threshold)
        }

        return Result(
            host=task.host,
            result={
                'total': len(interfaces),
                'problematic': len(problems),
                'details': problems,
            }
        )

    except Exception as e:
        logger.error(f"{task.host.name}: {e}")
        return Result(host=task.host, result=str(e), failed=True)


def main():
    parser = argparse.ArgumentParser(
        description='Collect interface error statistics from network devices'
    )
    parser.add_argument('--inventory', default='inventory.yaml',
                        help='Nornir inventory file')
    parser.add_argument('--devices', default='all',
                        help='Device list (comma-separated) or "all"')
    parser.add_argument('--error-threshold', type=int, default=10,
                        help='Error threshold for flagging (default: 10)')
    parser.add_argument('--interface-filter', default=None,
                        help='Regex to filter interface names')
    parser.add_argument('--output', default=None,
                        help='Write report to file')

    args = parser.parse_args()

    try:
        nr = InitNornir(config_file=args.inventory)

        if args.devices.lower() != 'all':
            devices = [d.strip() for d in args.devices.split(',')]
            nr = nr.filter(F(name__in=devices))

        logger.info(f"Collecting errors from {len(nr.inventory.hosts)} device(s)")

        results = nr.run(
            task=collect_interface_errors,
            threshold=args.error_threshold,
            if_filter=args.interface_filter
        )

        lines = ["Interface Error Report", "=" * 50]
        for host, task_results in results.items():
            tr = task_results[0]
            if tr.failed:
                lines.append(f"\n{host}: ERROR - {tr.result}")
                continue

            data = tr.result
            lines.append(f"\n{host}:")
            lines.append(f"  Total interfaces: {data['total']}")
            lines.append(f"  Problematic: {data['problematic']}")

            for if_name, iface in data['details'].items():
                lines.append(f"    {if_name} ({iface.status})")
                if iface.crc_errors:
                    lines.append(f"      CRC: {iface.crc_errors}")
                if iface.input_errors:
                    lines.append(f"      Input: {iface.input_errors}")
                if iface.output_errors:
                    lines.append(f"      Output: {iface.output_errors}")
                if iface.discards:
                    lines.append(f"      Discards: {iface.discards}")

        report = "\n".join(lines)
        print(report)

        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
            logger.info(f"Report written to {args.output}")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```