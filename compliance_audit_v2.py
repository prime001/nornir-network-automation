```python
"""
Network Configuration Validator

Audits critical network configuration settings across device inventory.
Validates presence and configuration of NTP, syslog, SNMP, DNS, logging,
and interface documentation.

Usage:
    python config_validator.py --inventory inventory
    python config_validator.py --site production --output validation_report.txt
    python config_validator.py --vendor cisco --strict

Prerequisites:
    - Nornir with NETMIKO and NAPALM: pip install nornir nornir-netmiko napalm
    - Network devices SSH-accessible
    - Inventory YAML files (hosts, groups, defaults)
    - Device credentials in environment or Nornir defaults
"""

import logging
import argparse
import json
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir.core.filter import F


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_ntp_config(task: Task) -> Result:
    """Validate NTP server configuration."""
    try:
        cmd = "show run | include ntp" if "cisco" in task.host.platform else "show ntp"
        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result if result else ""
        
        has_ntp = len(output.strip()) > 0 and "ntp server" in output.lower()
        return Result(host=task.host, result={"ntp_configured": has_ntp, "output": output})
    except Exception as e:
        logger.warning(f"NTP check failed for {task.host}: {str(e)}")
        return Result(host=task.host, result={"ntp_configured": False, "error": str(e)})


def validate_syslog_config(task: Task) -> Result:
    """Validate syslog server configuration."""
    try:
        cmd = "show run | include logging" if "cisco" in task.host.platform else "show logging"
        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result if result else ""
        
        has_syslog = "logging" in output.lower() and ("server" in output.lower() or "host" in output.lower())
        return Result(host=task.host, result={"syslog_configured": has_syslog})
    except Exception as e:
        logger.warning(f"Syslog check failed for {task.host}: {str(e)}")
        return Result(host=task.host, result={"syslog_configured": False, "error": str(e)})


def validate_snmp_config(task: Task) -> Result:
    """Validate SNMP configuration."""
    try:
        cmd = "show run | include snmp" if "cisco" in task.host.platform else "show snmp"
        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result if result else ""
        
        has_snmp = "snmp" in output.lower() and "community" in output.lower()
        return Result(host=task.host, result={"snmp_configured": has_snmp})
    except Exception as e:
        logger.warning(f"SNMP check failed for {task.host}: {str(e)}")
        return Result(host=task.host, result={"snmp_configured": False, "error": str(e)})


def validate_dns_config(task: Task) -> Result:
    """Validate DNS server configuration."""
    try:
        cmd = "show run | include dns" if "cisco" in task.host.platform else "show dns"
        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result if result else ""
        
        has_dns = "ip name-server" in output or "dns server" in output.lower()
        return Result(host=task.host, result={"dns_configured": has_dns})
    except Exception as e:
        logger.warning(f"DNS check failed for {task.host}: {str(e)}")
        return Result(host=task.host, result={"dns_configured": False, "error": str(e)})


def validate_interface_descriptions(task: Task) -> Result:
    """Check that interfaces have meaningful descriptions."""
    try:
        cmd = "show interface description" if "cisco" in task.host.platform else "show interfaces descriptions"
        result = task.run(netmiko_send_command, command_string=cmd)
        output = result[0].result if result else ""
        
        lines = output.split('\n')
        described = sum(1 for line in lines if len(line.strip()) > 20)
        
        return Result(host=task.host, result={"interfaces_documented": described > 3})
    except Exception as e:
        logger.warning(f"Interface check failed for {task.host}: {str(e)}")
        return Result(host=task.host, result={"interfaces_documented": False, "error": str(e)})


def validate_device_config(task: Task) -> Result:
    """Run all validation checks on a device."""
    checks = {}
    
    ntp_result = task.run(validate_ntp_config)
    checks['ntp'] = ntp_result[0].result.get("ntp_configured", False)
    
    syslog_result = task.run(validate_syslog_config)
    checks['syslog'] = syslog_result[0].result.get("syslog_configured", False)
    
    snmp_result = task.run(validate_snmp_config)
    checks['snmp'] = snmp_result[0].result.get("snmp_configured", False)
    
    dns_result = task.run(validate_dns_config)
    checks['dns'] = dns_result[0].result.get("dns_configured", False)
    
    iface_result = task.run(validate_interface_descriptions)
    checks['interfaces'] = iface_result[0].result.get("interfaces_documented", False)
    
    all_passed = all(checks.values())
    
    return Result(host=task.host, result={"checks": checks, "compliant": all_passed})


def run_validation(nr, strict=False):
    """Execute validation across all devices."""
    logger.info(f"Validating configuration on {len(nr.inventory.hosts)} devices...")
    
    results = nr.run(task=validate_device_config, num_workers=4)
    
    report = {
        "summary": {"total": len(nr.inventory.hosts), "compliant": 0, "noncompliant": 0},
        "devices": {}
    }
    
    for device, task_result in results.items():
        if task_result[0].failed:
            report["devices"][device] = {"status": "error", "checks": {}}
            report["summary"]["noncompliant"] += 1
            continue
        
        checks = task_result[0].result.get("checks", {})
        compliant = task_result[0].result.get("compliant", False)
        
        report["devices"][device] = {
            "status": "compliant" if compliant else "noncompliant",
            "checks": checks
        }
        
        if compliant:
            report["summary"]["compliant"] += 1
        else:
            report["summary"]["noncompliant"] += 1
    
    return report


def format_report(report):
    """Format validation report for display."""
    lines = [
        "Network Configuration Validation Report",
        "=" * 50,
        f"\nSummary: {report['summary']['compliant']}/{report['summary']['total']} devices compliant\n"
    ]
    
    for device, result in report['devices'].items():
        icon = "✓" if result['status'] == "compliant" else "✗"
        lines.append(f"{icon} {device}: {result['status'].upper()}")
        
        for check, passed in result['checks'].items():
            check_icon = "  ✓" if passed else "  ✗"
            lines.append(f"{check_icon} {check}")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Validate critical network device configuration"
    )
    parser.add_argument(
        "--inventory", "-i",
        default="inventory",
        help="Nornir inventory directory (default: inventory)"
    )
    parser.add_argument(
        "--site", "-s",
        help="Filter by site"
    )
    parser.add_argument(
        "--vendor", "-v",
        help="Filter by vendor"
    )
    parser.add_argument(
        "--output", "-o",
        help="Write report to file"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format"
    )
    
    args = parser.parse_args()
    
    try:
        nr = InitNornir(config_file=f"{args.inventory}/config.yaml")
        
        if args.site:
            nr = nr.filter(F(site=args.site))
        if args.vendor:
            nr = nr.filter(F(vendor=args.vendor))
        
        report = run_validation(nr)
        
        if args.json:
            output = json.dumps(report, indent=2)
        else:
            output = format_report(report)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            logger.info(f"Report written to {args.output}")
        else:
            print(output)
        
        return 0 if report["summary"]["noncompliant"] == 0 else 1
    
    except Exception as e:
        logger.error(f"Validation failed: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
```