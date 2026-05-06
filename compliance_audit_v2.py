The file write needs your approval. Once granted, the script will be at `/opt/NetAutoCommitter/026_routing_auth_audit.py`.

The script audits OSPF, EIGRP, and BGP neighbor sessions for authentication configuration — a distinct angle from generic compliance audits. Key design choices:

- **Three separate task functions** (`check_ospf_auth`, `check_eigrp_auth`, `check_bgp_auth`), each run as a Nornir sub-task so failures are isolated per protocol, not per host
- **CI/CD mode** via `--fail-on-violation` exits with code 1 if any unauthenticated sessions are found
- **JSON output** with a structured summary block — easy to pipe into monitoring systems or store as an artifact
- **Protocol selection** — audit all three or target one with `--protocol ospf|eigrp|bgp`
- ~190 lines, within the 60–200 target