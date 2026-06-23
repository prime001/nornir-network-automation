interface_error_monitor.py - Interface Error and Drop Counter Analysis

Purpose:
    Collects interface error counters (CRC, input/output errors, drops, resets)
    from network devices via Nornir/Netmiko and flags interfaces that exceed
    configurable thresholds. Intended for proactive fault detection and
    troubleshooting — distinct from basic interface status reporting.

Usage:
    python interface_error_monitor.py --hosts router1 router2 --username admin --password secret
    python interface_error_monitor.py --hosts core-sw1 --threshold-errors 50 --threshold-drops 200
    python interface_error_monitor.py --hosts fw1 --output-format json --output-file errors.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Devices must be reachable via SSH and support 'show interfaces' with TextFSM parsing.
"""

import argparse
import io
import json
import logging
import sys

from nornir import InitNornir
from nornir.core.inventory import Defaults, Groups, Host, Hosts, Inventory
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logger = logging.getLogger(__name__)


def collect_interface_counters(task: Task) -> Result:
    """Pull interface counters using TextFSM-parsed 'show interfaces'."""
    result = task.run(
        task=netmiko_send_command,
        command_string="show interfaces",
        use_textfsm=True,
    )
    return Result(host=task.host, result=result.result)


def parse_error_counters(raw: list) -> list:
    """Normalize TextFSM output rows into counter dicts with safe int coercion."""
    counters = []
    for iface in raw:
        counters.append({
            "interface": iface.get("interface", ""),
            "link_status": iface.get("link_status", "unknown"),
            "protocol_status": iface.get("protocol_status", "unknown"),
            "input_errors": int(iface.get("input_errors", 0) or 0),
            "output_errors": int(iface.get("output_errors", 0) or 0),
            "crc": int(iface.get("crc", 0) or 0),
            "input_drops": int(iface.get("input_drops", 0) or 0),
            "output_drops": int(iface.get("output_drops", 0) or 0),
            "resets": int(iface.get("resets", 0) or 0),
        })
    return counters


def flag_troubled(counters: list, threshold_errors: int, threshold_drops: int) -> list:
    """Return interfaces whose totals meet or exceed either threshold."""
    result = []
    for iface in counters:
        total_errors = iface["input_errors"] + iface["output_errors"] + iface["crc"]
        total_drops = iface["input_drops"] + iface["output_drops"]
        if total_errors >= threshold_errors or total_drops >= threshold_drops:
            result.append({**iface, "total_errors": total_errors, "total_drops": total_drops})
    return result


def build_report(nr_results, threshold_errors: int, threshold_drops: int) -> dict:
    """Aggregate per-host Nornir results into a structured report dict."""
    report = {
        "thresholds": {"errors": threshold_errors, "drops": threshold_drops},
        "summary": {"total_devices": 0, "devices_with_issues": 0},
        "devices": {},
    }
    for host, multi_result in nr_results.items():
        report["summary"]["total_devices"] += 1
        if multi_result.failed:
            report["devices"][host] = {"error": str(multi_result.exception)}
            continue

        raw = multi_result[0].result
        if not isinstance(raw, list):
            report["devices"][host] = {"error": "TextFSM parse failed or unsupported platform"}
            continue

        counters = parse_error_counters(raw)
        troubled = flag_troubled(counters, threshold_errors, threshold_drops)
        if troubled:
            report["summary"]["devices_with_issues"] += 1

        report["devices"][host] = {
            "total_interfaces": len(counters),
            "flagged_count": len(troubled),
            "flagged": troubled,
        }
    return report


def print_text_report(report: dict) -> None:
    t = report["thresholds"]
    s = report["summary"]
    print(f"\n{'='*72}")
    print("Interface Error & Drop Counter Report")
    print(f"  Thresholds  —  errors >= {t['errors']}  |  drops >= {t['drops']}")
    print(f"  Devices polled: {s['total_devices']}  |  devices with issues: {s['devices_with_issues']}")
    print(f"{'='*72}")

    for host, data in report["devices"].items():
        if "error" in data:
            print(f"\n[{host}] ERROR: {data['error']}")
            continue

        print(f"\n[{host}]  flagged: {data['flagged_count']}/{data['total_interfaces']} interfaces")
        if not data["flagged"]:
            print("  All interfaces within thresholds.")
            continue

        hdr = (
            f"  {'Interface':<26} {'InErr':>7} {'OutErr':>7} {'CRC':>7}"
            f" {'InDrop':>7} {'OutDrop':>8} {'Resets':>7}  Status"
        )
        print(hdr)
        print(f"  {'-'*90}")
        for iface in data["flagged"]:
            status = f"{iface['link_status']}/{iface['protocol_status']}"
            print(
                f"  {iface['interface']:<26}"
                f" {iface['input_errors']:>7}"
                f" {iface['output_errors']:>7}"
                f" {iface['crc']:>7}"
                f" {iface['input_drops']:>7}"
                f" {iface['output_drops']:>8}"
                f" {iface['resets']:>7}"
                f"  {status}"
            )

    print(f"\n{'='*72}\n")


def build_nornir(hosts, username, password, platform, port, num_workers):
    host_objects = Hosts()
    for h in hosts:
        host_objects[h] = Host(
            name=h,
            hostname=h,
            username=username,
            password=password,
            platform=platform,
            port=port,
        )
    inventory = Inventory(hosts=host_objects, groups=Groups(), defaults=Defaults())
    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": num_workers}},
        inventory=inventory,
        logging={"enabled": False},
    )


def main():
    parser = argparse.ArgumentParser(
        description="Flag interfaces with elevated error or drop counters across network devices."
    )
    parser.add_argument("--hosts", nargs="+", required=True, help="Hostnames or IPs to poll")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--platform", default="cisco_ios", help="Netmiko platform (default: cisco_ios)")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument(
        "--threshold-errors", type=int, default=0,
        help="Flag interface when total error count >= N (default: 0 = flag any error)",
    )
    parser.add_argument(
        "--threshold-drops", type=int, default=0,
        help="Flag interface when total drop count >= N (default: 0 = flag any drop)",
    )
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    parser.add_argument("--output-file", help="Write output to file instead of stdout")
    parser.add_argument("--num-workers", type=int, default=10)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    nr = build_nornir(
        args.hosts, args.username, args.password,
        args.platform, args.port, args.num_workers,
    )

    logger.info("Polling %d device(s)", len(args.hosts))
    results = nr.run(task=collect_interface_counters)

    report = build_report(results, args.threshold_errors, args.threshold_drops)

    if args.output_format == "json":
        output = json.dumps(report, indent=2)
        if args.output_file:
            with open(args.output_file, "w") as f:
                f.write(output)
            print(f"Report written to {args.output_file}")
        else:
            print(output)
    else:
        if args.output_file:
            buf = io.StringIO()
            old_stdout, sys.stdout = sys.stdout, buf
            print_text_report(report)
            sys.stdout = old_stdout
            with open(args.output_file, "w") as f:
                f.write(buf.getvalue())
            print(f"Report written to {args.output_file}")
        else:
            print_text_report(report)

    sys.exit(1 if report["summary"]["devices_with_issues"] else 0)


if __name__ == "__main__":
    main()