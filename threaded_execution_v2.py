Here is the script:

```python
"""NTP Synchronization Verifier

Connects to network devices via Nornir and verifies NTP synchronization status
across the inventory. Reports stratum level, reference clock, and offset for
each device, and flags any device that is unsynchronized or unreachable.

Usage:
    python ntp_check.py --group core --format table
    python ntp_check.py --devices router1 router2 --format json
    python ntp_check.py --group distribution --stratum-warn 3

Prerequisites:
    - Nornir inventory configured at ./nornir_config.yaml
    - Device SSH credentials configured in inventory
    - netmiko driver installed for target device types
    - Python packages: nornir, nornir-netmiko, pyyaml

Output:
    - Table or JSON report of NTP sync status per device
    - Stratum, reference server, and offset columns
    - Exit code 1 if any device is unsynchronized or unreachable
"""

import json
import logging
import argparse
import re
from typing import Dict, Any, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def collect_ntp_data(task: Task) -> Result:
    """Collect NTP status and associations from a single device."""
    status_result = task.run(
        task=netmiko_send_command,
        command_string='show ntp status',
        name='ntp_status',
    )
    assoc_result = task.run(
        task=netmiko_send_command,
        command_string='show ntp associations',
        name='ntp_associations',
    )
    return Result(
        host=task.host,
        result={
            'status': status_result.result,
            'associations': assoc_result.result,
        }
    )


def parse_ntp_status(output: str) -> Dict[str, Any]:
    """Extract sync state, stratum, reference, and offset from 'show ntp status'."""
    data: Dict[str, Any] = {
        'synced': False,
        'stratum': None,
        'reference': '',
        'offset_ms': None,
    }

    first_line = output.splitlines()[0].lower() if output.strip() else ''
    data['synced'] = 'synchronized' in first_line and 'unsynchronized' not in first_line

    m = re.search(r'stratum\s+(\d+)', output, re.IGNORECASE)
    if m:
        data['stratum'] = int(m.group(1))

    m = re.search(r'reference\s+is\s+(\S+)', output, re.IGNORECASE)
    if m:
        data['reference'] = m.group(1).rstrip(',')

    m = re.search(r'offset\s+([-\d.]+)', output, re.IGNORECASE)
    if m:
        try:
            data['offset_ms'] = float(m.group(1))
        except ValueError:
            pass

    return data


def parse_ntp_servers(output: str) -> list:
    """Extract configured NTP server IPs from 'show ntp associations'."""
    servers = []
    for line in output.splitlines():
        stripped = line.lstrip('*+~- \t')
        ip_match = re.match(r'^(\d{1,3}(?:\.\d{1,3}){3})', stripped)
        if ip_match:
            servers.append(ip_match.group(1))
    return servers


def format_table(device: str, info: Dict[str, Any], stratum_warn: int) -> None:
    """Print a single device row to the console table."""
    synced_str = 'YES' if info.get('synced') else 'NO '
    stratum = info.get('stratum')
    stratum_str = str(stratum) if stratum is not None else 'N/A'
    offset = info.get('offset_ms')
    offset_str = f'{offset:.3f}' if offset is not None else 'N/A'
    reference = info.get('reference') or 'N/A'
    servers = ', '.join(info.get('servers', [])[:2]) or 'none'
    warn = ''
    if not info.get('synced'):
        warn = '  [!] UNSYNCED'
    elif stratum is not None and stratum > stratum_warn:
        warn = f'  [!] STRATUM>{stratum_warn}'
    print(
        f"{device:<22} {synced_str:<8} {stratum_str:<9} "
        f"{reference:<18} {offset_str:<12} {servers}{warn}"
    )


def print_table_format(results: Dict[str, Any], stratum_warn: int) -> None:
    """Render full results table to stdout."""
    header = (
        f"{'DEVICE':<22} {'SYNCED':<8} {'STRATUM':<9} "
        f"{'REFERENCE':<18} {'OFFSET(ms)':<12} SERVERS"
    )
    print(f"\n{header}")
    print('-' * len(header))
    for device in sorted(results):
        info = results[device]
        if info.get('error'):
            print(f"{device:<22} ERROR: {info['error']}")
        else:
            format_table(device, info, stratum_warn)
    print()


def print_json_format(results: Dict[str, Any]) -> None:
    """Dump full results as JSON."""
    print(json.dumps(results, indent=2, default=str))


def build_results(nr_results, stratum_warn: int) -> Dict[str, Any]:
    """Aggregate per-host results into a flat dict."""
    out = {}
    for device_name, host_result in nr_results.items():
        if host_result.failed:
            out[device_name] = {'error': str(host_result.exception or 'task failed')}
            continue
        raw = host_result[0].result
        ntp_info = parse_ntp_status(raw.get('status', ''))
        ntp_info['servers'] = parse_ntp_servers(raw.get('associations', ''))
        out[device_name] = ntp_info
    return out


def main() -> int:
    """Execute NTP verification across selected inventory."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--group', help='Filter devices by inventory group')
    parser.add_argument('--devices', nargs='+', help='Specific device names (space-separated)')
    parser.add_argument('--format', choices=['table', 'json'], default='table',
                        help='Output format (default: table)')
    parser.add_argument('--stratum-warn', type=int, default=4,
                        help='Flag devices at or above this stratum (default: 4)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable debug logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if not args.group and not args.devices:
        logger.error('Specify either --group or --devices')
        parser.print_help()
        return 1

    try:
        nr = InitNornir(config_file='nornir_config.yaml')

        if args.group:
            nr = nr.filter(F(groups__contains=args.group))
        else:
            nr = nr.filter(F(name__in=args.devices))

        if not nr.inventory.hosts:
            logger.error('No devices matched selection criteria')
            return 1

        logger.info(f'Checking NTP on {len(nr.inventory.hosts)} device(s)')
        nr_results = nr.run(task=collect_ntp_data)

        results = build_results(nr_results, args.stratum_warn)

        if args.format == 'table':
            print_table_format(results, args.stratum_warn)
        else:
            print_json_format(results)

        problem_count = sum(
            1 for v in results.values()
            if v.get('error') or not v.get('synced')
        )
        if problem_count:
            logger.warning(f'{problem_count} device(s) unsynchronized or unreachable')

        return 0 if problem_count == 0 else 1

    except FileNotFoundError:
        logger.error('Nornir config file not found (nornir_config.yaml)')
        return 1
    except Exception as e:
        logger.error(f'Unexpected error: {e}', exc_info=args.verbose)
        return 1


if __name__ == '__main__':
    exit(main())
```