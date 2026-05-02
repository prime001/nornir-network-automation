I need permission to write the file. Please approve the write to `/opt/NetAutoCommitter/014_config_backup_diff.py`.

The script is a **differential config backup** tool — it:
- Backs up running configs via Nornir + Netmiko across all platforms (IOS, EOS, NXOS, JunOS, Huawei)
- Compares each backup against the previous snapshot using `difflib.unified_diff`
- Saves timestamped `.cfg` + `.diff` files per host
- Reports changed/added/removed line counts with `log.warning` for any drift detected
- Supports `--host`, `--group`, `--workers`, `--verbose`, and `--output-dir` CLI flags
- ~160 lines, PEP 8, no duplication of `004_config_backup.py`'s basic backup approach