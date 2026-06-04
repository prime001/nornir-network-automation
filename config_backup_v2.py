The script is ready. Here's what it does:

**`config_diff.py`** — Config Drift Detection (distinct from the existing backup scripts)

- Loads baseline configs from a backup directory (produced by `config_backup.py`)
- Fetches live running configs via Nornir/Netmiko
- Diffs them with `difflib.unified_diff`, showing exactly what changed
- Supports `--output json` for pipeline integration
- Exits with code `1` if drift is found (CI/cron-friendly)
- `--update-baseline` flag overwrites the stored baseline when drift is detected
- Falls back to timestamped filenames (`hostname_2025-01-01.txt`) if no direct `hostname.txt` exists
- Platform-aware: maps `cisco_ios`, `nxos`, `juniper_junos`, `arista_eos` to the right show command

**Scope gap this fills:** the existing backup scripts *create* snapshots; this one *audits* them to catch unauthorized config changes — a common real-world NOC workflow.