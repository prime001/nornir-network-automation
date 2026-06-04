The file has been written. Here's the script — `inventory_health.py`, a concurrent ICMP + SSH reachability checker:

**What it does:**
- Runs both a ping check and an SSH handshake for every inventory host in parallel via Nornir's threaded runner
- Reports latency for both channels in a formatted table
- Exits 0 only if all hosts pass both checks (CI-friendly)
- Optionally writes JSON output for downstream tooling

**Key design choices:**
- Single nornir task (`reachability_check`) handles ping + SSH per host, so both run concurrently across hosts without separate passes
- `paramiko` used directly for SSH handshake timing rather than a full netmiko session — faster, no command execution needed
- `--filter-group` lets you scope to a subset of inventory (e.g. `core_routers`)
- `--workers 20` default is higher than typical nornir defaults since most time is I/O wait on unreachable hosts

**Distinct from existing scripts:** `inventory_ops.py`/`v2` do inventory manipulation; this script tests live reachability of the current inventory — a NOC health-check use case not covered elsewhere.