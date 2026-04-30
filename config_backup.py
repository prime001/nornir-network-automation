```python
"""
Network Configuration Backup Script using Nornir

Purpose:
This script automates the backup of device configurations from network devices using Nornir and NAPALM.
It retrieves the running configuration from each device and saves it to a file in a specified directory.

Usage:
python config_backup.py --inventory inventory.yaml --output backups/

Prerequisites:
1. Nornir and NAPALM installed (pip install nornir nornir_napalm)
2. Valid inventory file in YAML format with host credentials
3. Network devices configured to allow SSH access
"""

import argparse
import logging
import os
from pathlib import Path
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_napalm.plugins.tasks import napalm_get
import yaml

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("config_backup")

def backup_config(task: Task, output_dir: str) -> Result:
    """Backup device configuration using NAPALM."""
    try:
        result = task.run(
            task=napalm_get,
            getters=["config"],
            **task.host.data
        )
        config_data = result.result["config"]
        filename = Path(output_dir) / f"{task.host.name}.cfg"
        os.makedirs(output_dir, exist_ok=True)
        
        with open(filename, "w") as f:
            f.write(config_data.get("running", "No running config retrieved"))
        
        logger.info(f"Successfully backed up {task.host.name} to {filename}")
        return Result(host=task.host, result=f"Backup saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to backup {task.host.name}: {str(e)}")
        return Result(host=task.host, result=str(e), failed=True)

def main(inventory_file: str, output_dir: str, dry_run: bool = False):
    """Main function to execute configuration backup."""
    try:
        nr = InitNornir(config_file=inventory_file)
        if dry_run:
            logger.info("Dry run mode - No files will be written")
        else:
            nr.run(task=backup_config, output_dir=output_dir)
    except yaml.YAMLError as e:
        logger.error(f"Invalid inventory file: {str(e)}")
    except Exception as e:
        logger.error(f"Initialization error: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Network device configuration backup")
    parser.add_argument(
        "--inventory",
        default="inventory.yaml",
        help="Path to inventory YAML file (default: inventory.yaml)",
    )
    parser.add_argument(
        "--output",
        default="backups/",
        help="Output directory for configuration files (default: backups/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without writing files",
    )
    args = parser.parse_args()
    
    main(args.inventory, args.output, args.dry_run)
```