```python
"""
Device Configuration Comparison Tool - Compares current vs backed-up device configs.

Purpose:
    Retrieves device running configurations and compares them against previously
    backed-up versions. Reports configuration drift and changes between versions.
    Useful for change tracking, compliance auditing, and troubleshooting.

Usage:
    python config_compare.py --inventory inventory.yml --backup-dir ./backups

Prerequisites:
    - Nornir installed with netmiko plugin
    - Network device SSH access
    - Device credentials in inventory
    - Previously backed-up config files (or will create baseline)

Examples:
    python config_compare.py --inventory inventory.yml --backup-dir ./backups
    python config_compare.py --inventory inventory.yml --backup-dir ./backups --devices router1,router2
    python config_compare.py --inventory inventory.yml --backup-dir ./backups --save-backup
"""

import json
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Tuple
from difflib import unified_diff

from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko.tasks import netmiko_send_command


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_running_config(task) -> str:
    """Retrieve running configuration from device."""
    try:
        if "iosxr" in task.host.platform.lower():
            cmd = "show running-config"
        elif "eos" in task.host.platform.lower():
            cmd = "show running-config"
        else:
            cmd = "show running-config"
        
        result = task.run(netmiko_send_command, command_string=cmd)
        return result[0].result if result else ""
    except Exception as e:
        logger.error(f"Failed to get config from {task.host.name}: {e}")
        raise


def load_backup_config(backup_path: Path) -> str:
    """Load previously backed-up configuration."""
    try:
        with open(backup_path, 'r') as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"Backup not found: {backup_path}")
        return ""


def save_config_backup(device_name: str, config: str, backup_dir: Path) -> None:
    """Save configuration to backup file."""
    backup_file = backup_dir / f"{device_name}.cfg"
    try:
        with open(backup_file, 'w') as f:
            f.write(config)
        logger.info(f"Backed up config for {device_name}")
    except Exception as e:
        logger.error(f"Failed to save backup for {device_name}: {e}")


def compare_configs(device_name: str, current: str, backup: str) -> Dict[str, Any]:
    """Compare current and backed-up configurations."""
    has_backup = bool(backup.strip())
    
    if not has_backup:
        return {
            "device": device_name,
            "timestamp": datetime.now().isoformat(),
            "status": "no_baseline",
            "message": "No previous backup to compare",
            "lines_changed": 0,
            "differences": []
        }
    
    current_lines = current.splitlines(keepends=True)
    backup_lines = backup.splitlines(keepends=True)
    
    diff = list(unified_diff(
        backup_lines, current_lines,
        fromfile='backup', tofile='current',
        lineterm=''
    ))
    
    changed = len(diff) > 0
    
    return {
        "device": device_name,
        "timestamp": datetime.now().isoformat(),
        "status": "changed" if changed else "unchanged",
        "message": f"{len(diff)} line(s) different" if changed else "Configuration unchanged",
        "lines_changed": len(diff),
        "differences": diff[:50]
    }


def analyze_devices(nr, backup_dir: Path, save_backup: bool = False, 
                   device_filter: List[str] = None) -> List[Dict[str, Any]]:
    """Analyze configuration changes across devices."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    results = []
    
    if device_filter:
        devices = nr.filter(F(name__in=device_filter))
    else:
        devices = nr
    
    for device_name, device in devices.inventory.hosts.items():
        result = {
            "device": device_name,
            "timestamp": datetime.now().isoformat(),
            "status": "error",
            "message": "Unknown error"
        }
        
        try:
            current_config = get_running_config(None)
            backup_config = load_backup_config(backup_dir / f"{device_name}.cfg")
            
            result = compare_configs(device_name, current_config, backup_config)
            
            if save_backup:
                save_config_backup(device_name, current_config, backup_dir)
            
            status_icon = "✓" if result["status"] == "unchanged" else "⚠"
            logger.info(f"{status_icon} {device_name:20s} {result['message']}")
        
        except Exception as e:
            result["message"] = str(e)
            logger.error(f"✗ {device_name:20s} Error: {str(e)}")
        
        results.append(result)
    
    return results


def print_comparison_report(results: List[Dict[str, Any]]) -> None:
    """Print formatted comparison report."""
    unchanged = sum(1 for r in results if r["status"] == "unchanged")
    changed = sum(1 for r in results if r["status"] == "changed")
    errors = sum(1 for r in results if r["status"] == "error")
    
    print("\n" + "=" * 70)
    print("Configuration Comparison Report")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    for result in results:
        status_icon = "✓" if result["status"] == "unchanged" else "⚠"
        if result["status"] == "error":
            status_icon = "✗"
        
        print(f"\n[{status_icon}] {result['device']:20s} {result['message']}")
        
        if result["status"] == "changed" and result.get("differences"):
            print(f"     First few changes (showing up to 10 lines):")
            for line in result["differences"][:10]:
                line_str = line.rstrip()
                if line_str.startswith('-'):
                    print(f"     - {line_str[1:]}")
                elif line_str.startswith('+'):
                    print(f"     + {line_str[1:]}")
    
    print("\n" + "=" * 70)
    print(f"Summary: {unchanged} unchanged, {changed} changed, {errors} errors")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Compare device configurations against backups"
    )
    parser.add_argument(
        "--inventory",
        default="inventory.yml",
        help="Path to Nornir inventory file"
    )
    parser.add_argument(
        "--backup-dir",
        default="./config_backups",
        help="Directory for configuration backups"
    )
    parser.add_argument(
        "--devices",
        help="Comma-separated device names to check"
    )
    parser.add_argument(
        "--save-backup",
        action="store_true",
        help="Save current configs as new backups"
    )
    parser.add_argument(
        "--output",
        help="Save results to JSON file"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        logger.info(f"Loading inventory from {args.inventory}")
        nr = InitNornir(config_file=args.inventory)
        
        backup_dir = Path(args.backup_dir)
        device_list = args.devices.split(",") if args.devices else None
        
        logger.info("Comparing device configurations...")
        results = analyze_devices(nr, backup_dir, args.save_backup, device_list)
        
        print_comparison_report(results)
        
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info(f"Results saved to {args.output}")
        
        changed_count = sum(1 for r in results if r["status"] == "changed")
        return 0 if changed_count == 0 else 1
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    exit(main())
```