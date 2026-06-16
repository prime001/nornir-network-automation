The script is ready. Here's what it does:

**`025_config_diff.py`** — Config Change Detection

- Connects to devices via Nornir + Netmiko, pulls running config
- Compares against a saved baseline file per device (`./baselines/<hostname>.txt`)
- Outputs a standard unified diff when drift is detected
- Creates an initial baseline if none exists (first run)
- `--update-baseline` flag overwrites the baseline after reporting drift
- Exits with code `0` (clean), `1` (failures), or `2` (changes detected) — useful for alerting pipelines
- Supports cisco_ios/xe/nxos/xr, junos, arista_eos platforms

This is distinct from the existing `config_backup*` scripts — those save snapshots, this detects and reports **change** between snapshots, which is the operational use case for drift detection and unauthorized change auditing.