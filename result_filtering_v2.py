Here's the script I'm writing — `arp_cache_filter.py`, a result-filtering script that collects ARP tables from multiple devices and filters by subnet (CIDR), MAC OUI prefix, or hostname regex. This is distinct from all existing scripts in the repo and demonstrates practical result-filtering patterns (per-host parse, multi-criteria filter, table/CSV output).

Key design choices:
- Supports `cisco_ios`, `cisco_xe`, `cisco_nxos`, `juniper_junos` with platform-specific `show arp` parsing
- Filters are composable (subnet + OUI can be combined)
- `--output csv` flattens all hosts into a single stream for piping to other tools
- `sys.exit(1)` on partial failure so CI/scripting picks up errors

Please approve the write above to create the file.