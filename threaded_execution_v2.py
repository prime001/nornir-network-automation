```python
"""
Device Facts Collector and Change Detector

Collects device facts (OS version, uptime, serial numbers, interfaces, etc.)
using NAPALM and stores them locally. Detects configuration/fact changes from
previous baseline collections.

Usage:
    python device_facts.py -i inventory.yaml -c credentials.yaml --output facts.json
    python device_facts.py --compare --output facts.json --previous facts_baseline.json

Prerequisites:
    - Nornir with NAPALM plugin installed
    - Network devices accessible via SSH/netconf
    - Inventory file in YAML format with device groups
    - Credentials provided via CLI arguments or environment variables
"""

import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from nornir import InitNornir
from nornir.core.filter import F
from nornir.plugins.tasks.napalm_utils import napalm_get


def setup_logging(verbose: bool) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=level,
    )


def collect_device_facts(norn: InitNornir, devices: Optional[str] = None) -> Dict[str, Any]:
    """Collect facts from devices using NAPALM get_facts."""
    logger = logging.getLogger("facts_collector")
    
    if devices:
        norn_filtered = norn.filter(F(name__contains=devices))
    else:
        norn_filtered = norn
    
    logger.info(f"Collecting facts from {len(norn_filtered.inventory.hosts)} device(s)")
    
    results = norn_filtered.run(
        task=napalm_get,
        getters=["facts", "interfaces", "interfaces_counters"],
    )
    
    facts_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "devices": {},
    }
    
    for device_name, task_result in results.items():
        if task_result.failed:
            logger.error(f"{device_name}: Task failed - {task_result[0].exception}")
            facts_data["devices"][device_name] = {"error": str(task_result[0].exception)}
            continue
        
        device_facts = {}
        for subtask_name, subtask_result in task_result.items():
            if subtask_result.failed:
                logger.warning(f"{device_name}/{subtask_name}: {subtask_result.exception}")
            else:
                device_facts.update({subtask_name: subtask_result.result})
        
        facts_data["devices"][device_name] = device_facts
        logger.info(f"{device_name}: Facts collected successfully")
    
    return facts_data


def detect_changes(
    current: Dict[str, Any], previous: Dict[str, Any]
) -> Dict[str, Any]:
    """Compare current facts against baseline and report changes."""
    logger = logging.getLogger("change_detector")
    changes = {"devices": {}}
    
    for device_name in current["devices"]:
        if device_name not in previous["devices"]:
            changes["devices"][device_name] = {"status": "new_device"}
            logger.info(f"{device_name}: New device detected")
            continue
        
        current_facts = current["devices"][device_name]
        previous_facts = previous["devices"][device_name]
        
        if current_facts == previous_facts:
            changes["devices"][device_name] = {"status": "unchanged"}
            continue
        
        device_changes = {"status": "changed", "differences": {}}
        
        current_top = set(current_facts.keys())
        previous_top = set(previous_facts.keys())
        
        for key in current_top - previous_top:
            device_changes["differences"][key] = {"type": "added"}
        
        for key in previous_top - current_top:
            device_changes["differences"][key] = {"type": "removed"}
        
        for key in current_top & previous_top:
            if current_facts[key] != previous_facts[key]:
                device_changes["differences"][key] = {
                    "type": "modified",
                    "previous": previous_facts[key],
                    "current": current_facts[key],
                }
        
        changes["devices"][device_name] = device_changes
        logger.warning(f"{device_name}: {len(device_changes['differences'])} change(s) detected")
    
    for device_name in previous["devices"]:
        if device_name not in current["devices"]:
            changes["devices"][device_name] = {"status": "device_unreachable"}
            logger.warning(f"{device_name}: Device no longer reachable")
    
    return changes


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "-i", "--inventory", default="inventory.yaml",
        help="Path to Nornir inventory file (default: inventory.yaml)"
    )
    parser.add_argument(
        "-c", "--credentials", default=".env",
        help="Path to credentials file or .env (default: .env)"
    )
    parser.add_argument(
        "-d", "--devices", help="Filter devices by name pattern"
    )
    parser.add_argument(
        "-o", "--output", default="facts.json",
        help="Output file for collected facts (default: facts.json)"
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Compare against previous baseline and detect changes"
    )
    parser.add_argument(
        "--previous", default="facts_baseline.json",
        help="Path to previous facts baseline (default: facts_baseline.json)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("main")
    
    try:
        norn = InitNornir(config_file=args.inventory)
        logger.info(f"Loaded inventory with {len(norn.inventory.hosts)} host(s)")
    except Exception as e:
        logger.error(f"Failed to initialize Nornir: {e}")
        return
    
    try:
        current_facts = collect_device_facts(norn, args.devices)
    except Exception as e:
        logger.error(f"Fact collection failed: {e}")
        return
    
    output_path = Path(args.output)
    output_path.write_text(json.dumps(current_facts, indent=2))
    logger.info(f"Facts written to {output_path}")
    
    if args.compare:
        previous_path = Path(args.previous)
        if not previous_path.exists():
            logger.warning(f"Baseline not found at {previous_path}, skipping comparison")
            return
        
        try:
            previous_facts = json.loads(previous_path.read_text())
            changes = detect_changes(current_facts, previous_facts)
            
            changes_path = Path(args.output.replace(".json", "_changes.json"))
            changes_path.write_text(json.dumps(changes, indent=2))
            logger.info(f"Change report written to {changes_path}")
        except Exception as e:
            logger.error(f"Change detection failed: {e}")


if __name__ == "__main__":
    main()
```