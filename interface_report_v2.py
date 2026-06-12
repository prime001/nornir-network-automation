Interface Error Counter Monitor
================================
Collects input/output error counters from network device interfaces and
flags those exceeding configurable thresholds. Useful for identifying
degraded links, duplex mismatches, and layer-1 physical problems.

Usage:
    # Single device
    python interface_error_monitor.py --host 192.168.1.1 --username admin --password secret

    # Inventory file, flag interfaces with >10 CRC errors
    python interface_error_monitor.py --inventory hosts.yaml --crc 10

    # All thresholds, multiple groups
    python interface_error_monitor.py --inventory hosts.yaml --groups core dist \
        --input-errors 100 --output-errors 50 --crc 5 --resets 10

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils

Exit code 0 = all interfaces within thresholds; 1 = violations found.
"""

import argparse
import logging
import re
import sys

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)


def _extract_int(text: str, pattern: str) -> int:
    m = re.search(pattern, text, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def parse_errors(output: str) -> list:
    interfaces = []
    for block in re.split(r'\n(?=\S)', output.strip()):
        lines = block.strip().splitlines()
        if not lines:
            continue
        m = re.match(r'^(\S+)\s+is\s+(\w+)', lines[0])
        if not m:
            continue
        text = '\n'.join(lines)
        interfaces.append({
            'name': m.group(1),
            'status': m.group(2),
            'input_errors': _extract_int(text, r'(\d+) input errors'),
            'output_errors': _extract_int(text, r'(\d+) output errors'),
            'crc': _extract_int(text, r'(\d+) CRC'),
            'resets': _extract_int(text, r'(\d+) interface resets'),
        })
    return interfaces


def check_interface_errors(task: Task, thresholds: dict) -> Result:
    cmd = task.run(task=netmiko_send_command, command_string='show interfaces')
    interfaces = parse_errors(cmd[0].result)

    flagged = []
    for intf in interfaces:
        violations = [
            f"{k}={intf[k]}"
            for k in ('input_errors', 'output_errors', 'crc', 'resets')
            if intf[k] > thresholds[k]
        ]
        if violations:
            flagged.append({**intf, 'violations': violations})

    return Result(
        host=task.host,
        result={'total': len(interfaces), 'flagged': flagged},
        failed=bool(flagged),
    )


def _build_single_inventory(host, username, password, platform, port):
    return Inventory(
        hosts=Hosts({host: Host(
            name=host, hostname=host, username=username,
            password=password, platform=platform, port=port,
        )}),
        groups=Groups(),
        defaults=Defaults(),
    )


def print_report(results, thresholds: dict) -> bool:
    any_violations = False
    sep = '=' * 62
    print(f"\n{sep}")
    print("INTERFACE ERROR COUNTER REPORT")
    thresh_str = '  '.join(f"{k}>{v}" for k, v in thresholds.items() if v >= 0)
    print(f"Thresholds: {thresh_str}")
    print(f"{sep}\n")

    for hostname, multi in results.items():
        host_result = multi[0]
        if host_result.exception:
            print(f"[{hostname}] ERROR: {host_result.exception}\n")
            continue

        data = host_result.result or {}
        flagged = data.get('flagged', [])
        total = data.get('total', 0)

        print(f"Host: {hostname}")
        print(f"  Interfaces checked : {total}")
        print(f"  Interfaces flagged : {len(flagged)}")

        if flagged:
            any_violations = True
            print(f"\n  {'Interface':<26} {'Status':<9} Violations")
            print(f"  {'-'*58}")
            for intf in flagged:
                print(f"  {intf['name']:<26} {intf['status']:<9} "
                      f"{', '.join(intf['violations'])}")
        else:
            print("  All interfaces within thresholds.")
        print()

    return any_violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Flag network interfaces with elevated error counters',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--host', help='Single device hostname or IP')
    source.add_argument('--inventory', metavar='FILE',
                        help='Nornir SimpleInventory hosts YAML file')

    parser.add_argument('--username', '-u', default='admin')
    parser.add_argument('--password', '-p', default='admin')
    parser.add_argument('--platform', default='cisco_ios')
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--groups', nargs='+',
                        help='Inventory groups to target (inventory mode only)')
    parser.add_argument('--workers', type=int, default=10)
    parser.add_argument('--debug', action='store_true')

    t = parser.add_argument_group('error thresholds (flag interfaces ABOVE these values)')
    t.add_argument('--input-errors', dest='input_errors', type=int, default=0)
    t.add_argument('--output-errors', dest='output_errors', type=int, default=0)
    t.add_argument('--crc', type=int, default=0)
    t.add_argument('--resets', type=int, default=0)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    thresholds = {
        'input_errors': args.input_errors,
        'output_errors': args.output_errors,
        'crc': args.crc,
        'resets': args.resets,
    }

    runner_cfg = {'plugin': 'threaded', 'options': {'num_workers': args.workers}}

    if args.host:
        nr = InitNornir(
            runner=runner_cfg,
            inventory={'plugin': 'SimpleInventory'},
            logging={'enabled': False},
        )
        nr.inventory = _build_single_inventory(
            args.host, args.username, args.password, args.platform, args.port
        )
    else:
        nr = InitNornir(
            runner=runner_cfg,
            inventory={
                'plugin': 'SimpleInventory',
                'options': {'host_file': args.inventory},
            },
            logging={'enabled': False},
        )
        if args.groups:
            nr = nr.filter(lambda h: any(g in h.groups for g in args.groups))

    if not nr.inventory.hosts:
        print("No hosts matched. Check --inventory/--groups arguments.", file=sys.stderr)
        return 2

    logger.info("Polling %d host(s)", len(nr.inventory.hosts))
    results = nr.run(
        task=check_interface_errors,
        thresholds=thresholds,
        name='interface_error_monitor',
    )

    return 1 if print_report(results, thresholds) else 0


if __name__ == '__main__':
    sys.exit(main())