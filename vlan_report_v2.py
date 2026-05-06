I need write permission to create the file at `/opt/NetAutoCommitter/repos/nornir-network-automation/027_vlan_report.py`. Please approve the write request above.

The script I'm writing focuses on **cross-device VLAN consistency auditing** — a distinct angle from basic VLAN listing that the earlier scripts likely cover. It detects:
1. **Missing VLANs** — defined on some switches but not all (breaks inter-switch L2 reachability)
2. **Pruned trunks** — VLAN defined locally but absent from trunk allowed lists
3. **Ghost VLANs** — VLAN ID in a trunk's allowed list but not in the VLAN database