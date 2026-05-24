I'll write the BGP origin validation script directly — distinct from the existing bgp_summary scripts which cover neighbor state/session summaries.

```python
"""
BGP Route Origin Validator

Purpose:
    Audit live BGP routing tables across network devices and validate that prefixes
    are being received from their expected origin ASNs. Detects potential route
    hijacks, misconfigurations, and unexpected path changes by comparing observed
    BGP data against a declarative policy file.

    Unlike a BGP summary (which shows neighbor state and session counts), this tool
    focuses on the data plane — what prefixes are actually installed and where they
    claim to originate.

Usage:
    python bgp_origin_validator.py -i inventory.yaml -p policy.yaml
    python bgp_origin_validator.py -i inventory.yaml -p policy.yaml --hosts r1,r2
    python bgp_origin_validator.py -i inventory.yaml -p policy.yaml --json-out out.json

Policy file format (YAML):
    prefixes:
      10.0.0.0/8: 65001
      192.168.100.0/24: 65002

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils pyyaml
    Nornir inventory files: hosts.yaml, groups.yaml, defaults.yaml
    SSH access to IOS/IOS-XE devices
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PrefixViolation:
    prefix: str
    expected_origin_asn: int
    actual_origin_asn: int
    as_path: str


@dataclass
class HostResult:
    host: str
    prefixes_checked: int = 0
    prefixes_missing: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    error: Optional[str] = None


def parse_bgp_table(raw: str) -> dict:
    """
    Parse 'show ip bgp' text into {prefix: {origin_asn, as_path}}.
    Handles Cisco IOS/IOS-XE table format with status-code prefix lines.
    """
    routes = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Network", "BGP", "Total")):
            continue

        parts = stripped.split()
        if len(parts) < 5:
            continue

        prefix = None
        prefix_idx = None
        for i, token in enumerate(parts):
            if "/" in token and not token.startswith(("*", ">", "i", "s", "r", "S")):
                prefix = token
                prefix_idx = i
                break

        if prefix is None or prefix_idx is None:
            continue

        try:
            # After prefix: next-hop, metric, locpref, weight, AS-path..., origin-code
            tail = parts[prefix_idx + 1:]
            # Skip next-hop, metric, locpref, weight (up to 4 numeric fields)
            skip = 0
            for t in tail[:4]:
                try:
                    int(t)
                    skip += 1
                except ValueError:
                    break
            path_tokens = tail[skip:]
            origin_code = path_tokens[-1] if path_tokens else "?"
            as_path_tokens = path_tokens[:-1] if len(path_tokens) > 1 else []
            origin_asn = int(as_path_tokens[-1]) if as_path_tokens else 0
            routes[prefix] = {
                "origin_asn": origin_asn,
                "as_path": " ".join(as_path_tokens),
                "origin_code": origin_code,
            }
        except (ValueError, IndexError):
            continue

    return routes


def validate_origins(task: Task, policy: dict) -> Result:
    """Nornir task: collect BGP table and check prefixes against policy."""
    result = HostResult(host=task.host.name)

    try:
        cmd_result = task.run(
            task=netmiko_send_command,
            command_string="show ip bgp",
            use_textfsm=False,
        )
        routes = parse_bgp_table(cmd_result.result)
    except Exception as exc:
        result.error = str(exc)
        logger.error("%s: collection failed — %s", task.host.name, exc)
        return Result(host=task.host, result=asdict(result))

    logger.debug("%s: parsed %d prefixes from BGP table", task.host.name, len(routes))

    for prefix, expected_asn in policy.items():
        result.prefixes_checked += 1

        if prefix not in routes:
            result.prefixes_missing.append(prefix)
            logger.warning("%s: expected prefix %s not in BGP table", task.host.name, prefix)
            continue

        actual_asn = routes[prefix]["origin_asn"]
        if actual_asn != expected_asn:
            v = PrefixViolation(
                prefix=prefix,
                expected_origin_asn=expected_asn,
                actual_origin_asn=actual_asn,
                as_path=routes[prefix]["as_path"],
            )
            result.violations.append(asdict(v))
            logger.warning(
                "%s: ORIGIN MISMATCH %s — expected AS%d got AS%d (path: %s)",
                task.host.name, prefix, expected_asn, actual_asn, routes[prefix]["as_path"],
            )

    return Result(host=task.host, result=asdict(result))


def load_policy(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        logger.error("Policy file not found: %s", path)
        sys.exit(1)
    with p.open() as fh:
        data = yaml.safe_load(fh)
    return data.get("prefixes", data)


def print_summary(results: list) -> int:
    total_violations = sum(len(r.get("violations", [])) for r in results)
    total_missing = sum(len(r.get("prefixes_missing", [])) for r in results)
    total_errors = sum(1 for r in results if r.get("error"))

    print("\n" + "=" * 65)
    print("BGP Route Origin Validation Results")
    print("=" * 65)

    for r in results:
        if r.get("error"):
            status = "ERROR"
        elif r.get("violations") or r.get("prefixes_missing"):
            status = "FAIL"
        else:
            status = "PASS"

        print(
            f"  {r['host']:<22} [{status:<5}]  "
            f"checked={r['prefixes_checked']}  "
            f"violations={len(r.get('violations', []))}  "
            f"missing={len(r.get('prefixes_missing', []))}"
        )
        if r.get("error"):
            print(f"    ! {r['error']}")
        for v in r.get("violations", []):
            print(
                f"    VIOLATION  {v['prefix']:<22} "
                f"expected=AS{v['expected_origin_asn']}  "
                f"actual=AS{v['actual_origin_asn']}  "
                f"path=[{v['as_path']}]"
            )
        for prefix in r.get("prefixes_missing", []):
            print(f"    MISSING    {prefix}")

    print(
        f"\nSummary: {len(results)} hosts | "
        f"{total_violations} origin violations | "
        f"{total_missing} missing prefixes | "
        f"{total_errors} collection errors"
    )
    return 1 if (total_violations or total_missing or total_errors) else 0


def main():
    parser = argparse.ArgumentParser(
        description="Validate BGP route origins against a declarative ASN policy"
    )
    parser.add_argument(
        "-i", "--inventory", default="inventory.yaml",
        help="Nornir config file pointing to hosts/groups/defaults YAML",
    )
    parser.add_argument(
        "-p", "--policy", required=True,
        help="YAML policy file: {prefixes: {prefix: origin_asn}}",
    )
    parser.add_argument(
        "--hosts", metavar="H1,H2",
        help="Comma-separated subset of inventory hosts to target",
    )
    parser.add_argument(
        "--json-out", metavar="FILE",
        help="Write full results as JSON to FILE",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    policy = load_policy(args.policy)
    if not policy:
        logger.error("Policy file loaded but contains no prefix rules")
        sys.exit(1)
    logger.info("Loaded %d prefix rules from policy", len(policy))

    nr = InitNornir(config_file=args.inventory)

    if args.hosts:
        target = {h.strip() for h in args.hosts.split(",")}
        nr = nr.filter(lambda host: host.name in target)

    if not nr.inventory.hosts:
        logger.error("No hosts matched — check inventory or --hosts filter")
        sys.exit(1)

    logger.info("Targeting %d host(s)", len(nr.inventory.hosts))
    run_results = nr.run(task=validate_origins, policy=policy)
    all_results = [r[0].result for r in run_results.values() if r]

    if args.json_out:
        out = Path(args.json_out)
        out.write_text(json.dumps(all_results, indent=2))
        logger.info("Results written to %s", out)

    sys.exit(print_summary(all_results))


if __name__ == "__main__":
    main()
```