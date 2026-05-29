The write was blocked by permissions. Please approve the write to `/opt/NetAutoCommitter/config_diff.py`, or let me know if you'd prefer a different path.

The script is a **configuration drift detector** — it fetches `show running-config` from each device, diffs it against a stored baseline using Python's `difflib.unified_diff`, and reports what changed. Key features:
- `--save` flag to capture/update baselines
- `--filter-groups` / `--filter-hosts` for scoped runs  
- `--no-diff` for summary-only output
- Exit code 1 if any devices changed or failed (CI/cron friendly)
- Parallel execution via nornir's threaded runner