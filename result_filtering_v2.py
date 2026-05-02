```python
"""Task result filtering and selective re-execution.

This script executes network tasks, filters results based on success/failure
patterns, and selectively re-runs failed tasks. Useful for reliable automation
with intelligent failure handling and progress tracking.

Usage:
    python 015_result_filtering_rerun.py --hosts all --commands "show version"
    python 015_result_filtering_rerun.py --group routers --retry-failed

Prerequisites:
    - nornir installed with netmiko/napalm backend
    - Configured inventory.yaml and hosts.yaml in project root
    - SSH/Telnet access with credentials in inventory or .env
"""

import logging
import argparse
import time
from typing import Dict, Tuple
from nornir import InitNornir
from nornir.core.filter import F
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command


logger = logging.getLogger(__name__)


def execute_commands(task: Task, commands: list) -> Result:
    """Execute network commands with timing and error tracking."""
    results = {}
    start_time = time.time()

    try:
        for cmd in commands:
            try:
                result = task.run(
                    netmiko_send_command,
                    command_string=cmd,
                    name=f"cmd_{cmd[:20]}",
                )
                results[cmd] = {
                    "success": not result.failed,
                    "output_length": len(result.result) if result.result else 0,
                }
            except Exception as e:
                results[cmd] = {
                    "success": False,
                    "error": str(e),
                }

        duration = time.time() - start_time
        success_count = sum(1 for r in results.values() if r.get("success"))

        return Result(
            host=task.host,
            result={
                "commands": results,
                "duration": duration,
                "success_rate": success_count / len(commands) if commands else 0,
            }
        )
    except Exception as e:
        return Result(
            host=task.host,
            failed=True,
            exception=e,
            result={"error": str(e), "duration": time.time() - start_time}
        )


def filter_results(nr_results) -> Tuple[Dict, Dict]:
    """Categorize results into successful and failed hosts."""
    successful = {}
    failed = {}

    for host_name, multi_result in nr_results.items():
        if multi_result.failed:
            failed[host_name] = {
                "reason": str(multi_result[0].exception),
                "attempts": 1,
            }
        else:
            data = multi_result[0].result
            success_rate = data.get("success_rate", 0)

            if success_rate == 1.0:
                successful[host_name] = data
            else:
                failed[host_name] = {
                    "reason": f"Partial success: {success_rate * 100:.0f}%",
                    "attempts": 1,
                }

    return successful, failed


def print_results(successful: Dict, failed: Dict, stage: str = "") -> None:
    """Print formatted result summary."""
    stage_label = f" ({stage})" if stage else ""
    total = len(successful) + len(failed)

    print("\n" + "=" * 70)
    print(f"RESULT FILTERING REPORT{stage_label}")
    print("=" * 70)
    print(f"Total Hosts: {total}")
    print(f"  ✓ Successful: {len(successful)} ({100*len(successful)//total if total else 0}%)")
    print(f"  ✗ Failed: {len(failed)} ({100*len(failed)//total if total else 0}%)")

    if failed:
        print(f"\nFailed Hosts:")
        for host, details in failed.items():
            print(f"  - {host}: {details['reason']} (Attempt {details['attempts']})")

    if successful:
        slow_hosts = [
            h for h, d in successful.items()
            if d.get("duration", 0) > 3
        ]
        if slow_hosts:
            print(f"\nSlow Hosts (>3s):")
            for host in slow_hosts:
                duration = successful[host].get("duration", 0)
                print(f"  - {host}: {duration:.2f}s")

    print("=" * 70 + "\n")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Execute network commands and filter results by success",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hosts",
        type=str,
        default="all",
        help="Target hosts: 'all', group name, or device name",
    )
    parser.add_argument(
        "--commands",
        type=str,
        nargs="+",
        default=["show version"],
        help="Commands to execute (space-separated)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry failed hosts once",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Command timeout in seconds",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        nr = InitNornir(config_file="config.yaml")

        if args.hosts != "all":
            nr = nr.filter(F(groups__contains=args.hosts) | F(name=args.hosts))

        if len(nr.inventory.hosts) == 0:
            logger.error(f"No hosts matched filter: {args.hosts}")
            return

        logger.info(
            f"Executing {len(args.commands)} command(s) on {len(nr.inventory.hosts)} hosts"
        )

        results = nr.run(
            task=execute_commands,
            commands=args.commands,
            name="Command Execution",
        )

        successful, failed = filter_results(results)
        print_results(successful, failed, "Initial Run")

        if args.retry_failed and failed:
            logger.info(f"Retrying {len(failed)} failed hosts")
            retry_hosts = nr.filter(F(name__in=list(failed.keys())))
            retry_results = retry_hosts.run(
                task=execute_commands,
                commands=args.commands,
                name="Command Execution Retry",
            )

            retry_successful, retry_failed = filter_results(retry_results)

            for host in retry_successful:
                if host in failed:
                    failed[host]["attempts"] = 2
                    successful[host] = retry_successful[host]

            for host in retry_failed:
                if host in failed:
                    failed[host]["attempts"] = 2

            print_results(successful, failed, "After Retry")

        logger.info("Result filtering completed successfully")

    except Exception as e:
        logger.error(f"Execution failed: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
```