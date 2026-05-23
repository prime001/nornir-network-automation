Writing an NTP compliance audit script — distinct from all existing scripts in the repo.

```python
"""
NTP Compliance Auditor

Purpose:
    Connects to network devices via Nornir/Netmiko, collects NTP association
    data, and reports synchronization status, stratum level, and reference
    server compliance across the fleet. Identifies unsynchronized devices,
    stratum violations, and unauthorized NTP peer usage.

Usage:
    python ntp_audit.py
    python ntp_audit.py --group core-routers --max-stratum 3
    python ntp_audit.py --hosts r1,r2 --required-server 10.0.0.1 --output json
    python ntp_audit.py --output csv > ntp_report.csv

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils
    Nornir inventory files: hosts.yaml, groups.yaml, defaults.yaml
    SSH access to devices; IOS/IOS-XE/EOS compatible output expected.
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class NTPStatus:
    hostname: str
    synced: bool = False
    stratum: int = 16
    reference: str = ""
    peers: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def label(self, max_stratum: int = 3, required_server: Optional[str] = None) -> str:
        if self.error and not self.synced:
            return "ERROR"
        if not self.synced:
            return "NOT SYNCED"
        issues = []
        if self.stratum > max_stratum:
            issues.append(f"STRATUM {self.stratum}")
        if required_server and required_server not in self.peers:
            issues.append("MISSING SERVER")
        return ", ".join(issues) if issues else "OK"


def parse_ntp_associations(output: str, hostname: str) -> NTPStatus:
    """Parse 'show ntp associations' from IOS/IOS-XE/EOS."""
    status = NTPStatus(hostname=hostname)
    peers: List[str] = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or re.match(r"^(address|~address|ind|ref)", stripped, re.I):
            continue

        # * in columns 0-2 marks the system peer (we're synchronized to it)
        is_sys_peer = "*" in line[:3]

        # remove all leading marker characters: * + - x ~ space
        clean = re.sub(r"^[*+\-x~\s]+", "", stripped)
        parts = clean.split()
        if len(parts) < 3:
            continue

        peer_addr = parts[0]
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", peer_addr):
            continue

        peers.append(peer_addr)

        if is_sys_peer:
            status.reference = peer_addr
            status.synced = True
            try:
                status.stratum = int(parts[2])
            except (ValueError, IndexError):
                pass

    status.peers = peers
    return status


def audit_ntp(task: Task, max_stratum: int, required_server: Optional[str]) -> Result:
    hostname = task.host.name
    try:
        sub = task.run(
            task=netmiko_send_command,
            command_string="show ntp associations",
            name="show ntp associations",
        )
        output = sub[0].result
    except Exception as exc:
        logger.warning("Failed to collect NTP data from %s: %s", hostname, exc)
        return Result(
            host=task.host,
            result=NTPStatus(hostname=hostname, error=str(exc)),
            failed=True,
        )

    status = parse_ntp_associations(output, hostname)
    status_label = status.label(max_stratum, required_server)
    if status_label not in ("OK", "ERROR"):
        logger.warning("%s: %s", hostname, status_label)

    return Result(host=task.host, result=status)


def render_table(statuses: List[NTPStatus], max_stratum: int, required_server: Optional[str]) -> None:
    widths = (22, 16, 8, 18, 7)
    header = (
        f"{'HOSTNAME':<{widths[0]}} {'STATUS':<{widths[1]}} "
        f"{'STRATUM':<{widths[2]}} {'REFERENCE':<{widths[3]}} {'PEERS':<{widths[4]}}"
    )
    print(header)
    print("-" * (sum(widths) + len(widths)))
    for s in statuses:
        lbl = s.label(max_stratum, required_server)
        print(
            f"{s.hostname:<{widths[0]}} {lbl:<{widths[1]}} "
            f"{s.stratum:<{widths[2]}} {s.reference:<{widths[3]}} "
            f"{len(s.peers):<{widths[4]}}"
        )


def render_json(statuses: List[NTPStatus], max_stratum: int, required_server: Optional[str]) -> None:
    rows = [
        {
            "hostname": s.hostname,
            "status": s.label(max_stratum, required_server),
            "synced": s.synced,
            "stratum": s.stratum,
            "reference": s.reference,
            "peers": s.peers,
            "error": s.error,
        }
        for s in statuses
    ]
    print(json.dumps(rows, indent=2))


def render_csv(statuses: List[NTPStatus], max_stratum: int, required_server: Optional[str]) -> None:
    print("hostname,status,synced,stratum,reference,peer_count,error")
    for s in statuses:
        err = (s.error or "").replace(",", ";")
        print(
            f"{s.hostname},{s.label(max_stratum, required_server)},"
            f"{s.synced},{s.stratum},{s.reference},{len(s.peers)},{err}"
        )


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit NTP synchronization compliance across network devices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--hosts", metavar="H1,H2", help="Comma-separated hostnames to target")
    p.add_argument("--group", metavar="GROUP", help="Nornir inventory group to target")
    p.add_argument(
        "--max-stratum", type=int, default=3, metavar="N",
        help="Flag devices with stratum > N (default: 3)",
    )
    p.add_argument(
        "--required-server", metavar="IP",
        help="Flag devices not peering with this NTP server",
    )
    p.add_argument(
        "--output", choices=["table", "json", "csv"], default="table",
    )
    p.add_argument("--config", default="config.yaml", help="Nornir config file")
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = build_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        nr = InitNornir(config_file=args.config, core={"num_workers": args.workers})
    except Exception as exc:
        logger.error("Nornir init failed: %s", exc)
        return 1

    if args.hosts:
        names = {h.strip() for h in args.hosts.split(",")}
        nr = nr.filter(filter_func=lambda h: h.name in names)
    elif args.group:
        nr = nr.filter(filter_func=lambda h: args.group in h.groups)

    if not nr.inventory.hosts:
        logger.error("No hosts matched.")
        return 1

    logger.info("Auditing %d host(s)", len(nr.inventory.hosts))

    results = nr.run(
        task=audit_ntp,
        max_stratum=args.max_stratum,
        required_server=args.required_server,
        name="NTP audit",
    )

    statuses: List[NTPStatus] = []
    for host, multi in results.items():
        top = multi[0]
        if isinstance(top.result, NTPStatus):
            statuses.append(top.result)
        else:
            statuses.append(NTPStatus(hostname=host, error="unexpected result"))

    statuses.sort(key=lambda s: s.hostname)

    if args.output == "json":
        render_json(statuses, args.max_stratum, args.required_server)
    elif args.output == "csv":
        render_csv(statuses, args.max_stratum, args.required_server)
    else:
        render_table(statuses, args.max_stratum, args.required_server)

    non_ok = [s for s in statuses if s.label(args.max_stratum, args.required_server) != "OK"]
    if non_ok and args.output == "table":
        print(f"\n{len(non_ok)} of {len(statuses)} device(s) non-compliant")

    return 1 if non_ok else 0


if __name__ == "__main__":
    sys.exit(main())
```