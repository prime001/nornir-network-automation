Since the write was denied, here is the script content directly:

```
"""
Config Change Detection — nornir-network-automation portfolio script.

Retrieves running configs from network devices and compares them against
previously saved baselines. Reports unified diffs for changed devices and
optionally updates the stored baseline.

Usage:
    python 015_config_backup.py --host 192.168.1.1 --username admin
    python 015_config_backup.py --host 192.168.1.1 --username admin --update
    python 015_config_backup.py --host 192.168.1.1 --username admin --baseline-dir /var/net/baselines

Prerequisites:
    pip install nornir nornir-netmiko nornir-utils netmiko
"""
```

Actually — to give you just the script content without fences as requested: