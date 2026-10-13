The user asked for the script content as output only — here it is:

```
"""
device_health.py - Network Device Health Monitor
...
```

Actually, the instructions say "Output ONLY the script content, no markdown fences, no explanation." I should just print the script as plain text. Let me output it directly:

---

"""
device_health.py - Network Device Health Monitor

Collects CPU utilization, memory usage, and uptime from Cisco IOS/NX-OS
devices using Nornir and Netmiko. Emits a formatted report and exits
non-zero if any device exceeds the configured warning thresholds.

Usage:
    python device_health.py
    python device_health.py --hosts spine1,spine2
    python device_health.py --group core --cpu-warn 70 --mem-warn 80
    python device_health.py --format json --output health.json

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory: hosts.yaml, groups.yaml, defaults.yaml
    Set device_type in host/group data (e.g. cisco_ios, cisco_nxos).
"""

import argparse
import contextlib
import json
import logging
import re
import sys
from datetime import datetime
from io import StringIO

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_cpu(output: str) -> float | None:
    # IOS: "five minutes: 12%/0%"
    m = re.search(r"five minutes:\s+(\d+)%", output, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # NX-OS: "5 Sec Average:  3%"
    m = re.search(r"5 Sec Average:\s+(\d+)%", output, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _parse_memory(output: str) -> dict | None:
    # IOS: "Total: 1073741824, Used: 536870912, Free: 536870912"
    m = re.search(r"Total:\s+(\d+),\s+Used:\s+(\d+)", output, re.IGNORECASE)
    if m:
        total, used = int(m.group(1)), int(m.group(2))
        pct = round(used / total * 100, 1) if total else 0.0
        return {"used_kb": used // 1024, "total_kb": total // 1024, "pct": pct}
    return None


def _parse_uptime(output: str) -> str:
    m = re.search(r"uptime is (.+?)(?:\n|$)", output, re.IGNORECASE)
    return m.group(1).strip() if m else "unknown"


def collect_health(task: Task, cpu_warn: int, mem_warn: int) -> Result:
    """Gather CPU, memory, and uptime for one device; flag threshold breaches."""
    data: dict = {
        "host": task.host.name,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cpu_pct": None,
        "memory": None,
        "uptime": "unknown",
        "alerts": [],
    }

    for cmd, key in [
        ("show processes cpu", "cpu"),
        ("show processes memory", "mem"),
        ("show version", "ver"),
    ]:
        try:
            out = task.run(
                task=netmiko_send_command,
                command_string=cmd,
                name=key,
            ).result
            if key == "cpu":
                data["cpu_pct"] = _parse_cpu(out)
                if data["cpu_pct"] is not None and data["cpu_pct"] >= cpu_warn:
                    data["alerts"].append(
                        f"CPU {data['cpu_pct']:.0f}% >= warn {cpu_warn}%"
                    )
            elif key == "mem":
                data["memory"] = _parse_memory(out)
                if data["memory"] and data["memory"]["pct"] >= mem_warn:
                    data["alerts"].append(
                        f"Memory {data['memory']['pct']:.0f}% >= warn {mem_warn}%"
                    )
            else:
                data["uptime"] = _parse_uptime(out)
        except Exception as exc:
            logger.warning("%s: %s failed: %s", task.host.name, cmd, exc)

    return Result(host=task.host, result=data)


def render_table(records: list[dict]) -> None:
    fmt = "{:<22} {:>7} {:>8} {:>8}  {}"
    header = fmt.format("Host", "CPU %", "Mem %", "Alerts", "Uptime")
    print(header)
    print("-" * (len(header) + 10))
    for rec in records:
        cpu = f"{rec['cpu_pct']:.1f}" if rec.get("cpu_pct") is not None else "n/a"
        mem = (
            f"{rec['memory']['pct']:.1f}"
            if rec.get("memory")
            else "n/a"
        )
        n_alerts = len(rec.get("alerts", []))
        flag = f"[{n_alerts}]" if n_alerts else "-"
        print(fmt.format(rec["host"], cpu, mem, flag, rec.get("uptime", "unknown")))
        for alert in rec.get("alerts", []):
            print(f"    ! {alert}")


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Report CPU/memory/uptime health across a Nornir inventory."
    )
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--hosts", help="Comma-separated hostnames to target")
    p.add_argument("--group", help="Nornir group to target")
    p.add_argument(
        "--cpu-warn", type=int, default=80, metavar="PCT",
        help="CPU %% alert threshold (default: 80)",
    )
    p.add_argument(
        "--mem-warn", type=int, default=85, metavar="PCT",
        help="Memory %% alert threshold (default: 85)",
    )
    p.add_argument(
        "--format", choices=["table", "json"], default="table",
        dest="output_format", help="Output format (default: table)",
    )
    p.add_argument("--output", metavar="FILE", help="Write report to file")
    p.add_argument(
        "--workers", type=int, default=20, help="Concurrent workers (default: 20)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = build_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    nr = InitNornir(
        config_file=args.config,
        runner={"plugin": "threaded", "options": {"num_workers": args.workers}},
    )

    if args.hosts:
        nr = nr.filter(F(name__any=args.hosts.split(",")))
    elif args.group:
        nr = nr.filter(F(groups__contains=args.group))

    if not nr.inventory.hosts:
        print("No hosts matched the given filters.", file=sys.stderr)
        sys.exit(1)

    print(f"Collecting health from {len(nr.inventory.hosts)} device(s) ...", flush=True)
    raw = nr.run(
        task=collect_health,
        cpu_warn=args.cpu_warn,
        mem_warn=args.mem_warn,
        name="device_health",
    )

    records = [
        r[0].result
        for r in raw.values()
        if not r[0].failed and isinstance(r[0].result, dict)
    ]

    failed = len(nr.inventory.hosts) - len(records)
    if not records:
        print("No data collected — check connectivity and credentials.", file=sys.stderr)
        sys.exit(2)

    alerted = sum(1 for r in records if r.get("alerts"))
    summary = (
        f"Done: {len(records)} collected, {alerted} with alerts"
        + (f", {failed} failed" if failed else "")
    )
    print(summary + "\n")

    if args.output_format == "json":
        output = json.dumps(records, indent=2)
    else:
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            render_table(records)
        output = buf.getvalue()

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(output)
        print(f"Report written to {args.output}")
    else:
        print(output)

    sys.exit(1 if alerted else 0)