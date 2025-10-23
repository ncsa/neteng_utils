# NetBox Tag-Based Backup Script

This script automates the backup of all active **devices** and **virtual machines (VMs)** in NetBox that share a specific **tag**.  
It retrieves the list of hosts via the NetBox API (using `pynetbox`), and performs per-host backups using **rsync** over SSH.  
If the backup directory for a host does not exist, it automatically creates a **ZFS filesystem** before running the rsync job.

---

##  Overview

The script performs the following steps:

1. **Connects to NetBox** using the supplied API URL and token.  
2. **Finds all active devices and VMs** with a specific tag.  
3. **Backs up each host** using `rsync`:
   - Uses a dedicated SSH key.
   - Reads excluded paths from a configured exclude file.
   - If the host’s backup path does not exist, it creates a new ZFS filesystem.
4. Logs all actions and errors to stdout for easy monitoring or cron integration.

---

## Configuration

Edit the configuration section at the top of the script before running:

```python
API_URL = "<URL>"                  # NetBox base URL (no trailing /api)
API_TOKEN = "<TOKEN>"              # NetBox API token
TAG = "<TAG>"                      # Tag used to select devices/VMs for backup
BACKUP_BASE_PATH = "/backup/path/dir"   # Base directory for all host backups
EXCLUDE_FILE = "/backup/path/exclude"   # File listing rsync exclude patterns
SSH_KEY = "/backup/path/key/.ssh/id_rsa" # Private key used for rsync SSH connections
```

---

## Directory Structure

Each host (device or VM) gets its own directory under the base backup path, e.g.:

```
/backup/path/dir/
├── host1/
├── host2/
└── host3/
```

If a backup directory doesn’t exist, the script will automatically create one using ZFS:

```bash
zfs create services/backup/servers/rsync_backup/<hostname>
```

---

## Authentication

Backups are done using SSH and `rsync` with key-based authentication.  
The remote host must have an account (e.g., `rsbackup`) configured for SSH access using the private key specified in `SSH_KEY`.

---

## Example Run

```bash
python3 server_backup.py
```

The script will:
- Fetch all active devices and VMs with the configured tag.
- Iterate through each host and back it up with rsync.
- Log progress and errors to the console.

Example output:

```
2025-10-23 11:02:10 - INFO - ########### Backing Up host1 #############
2025-10-23 11:02:10 - INFO - Backup path /backup/path/dir/host1 exists. Proceeding with rsync.
2025-10-23 11:03:45 - INFO - Backup for host1 completed successfully.
2025-10-23 11:03:45 - INFO - ########### Backing Up host2 #############
2025-10-23 11:03:46 - ERROR - !!!!!!!! Backup for host2 failed with exit code 256. !!!!!!!!
```

---

## Notes

- Devices must be in **“Active”** state in NetBox to be included.  
- Virtual Machines are also filtered by **tag** and **status == active**.  
- The exclude file allows fine-grained control over which paths are omitted from backup.  
- The script can be scheduled via cron for nightly or weekly runs.

Example cron entry:

```
0 2 * * * /usr/local/bin/python3 /services/scripts/netbox_backup_by_tag.py >> /var/log/netbox_backup.log 2>&1
```

---

## 🧾 Dependencies

- Python 3.7+
- [`pynetbox`](https://github.com/netbox-community/pynetbox)
- `rsync`, `ssh`, and `zfs` tools available on the system

Install `pynetbox` if not already present:

```bash
pip install pynetbox
```

---

## Error Handling

- API errors (e.g., invalid token or URL) are logged and skipped.  
- If rsync fails for a host, the exit code is logged for debugging.  
- ZFS creation errors are surfaced through the command output.

---

## Summary

| Function | Description |
|-----------|-------------|
| `get_online_entities_with_tag(nb, tag)` | Fetches all active devices and VMs with the specified tag. |
| `backup_device(server)` | Performs rsync-based backup for a single host, creating a ZFS filesystem if missing. |
| `__main__` | Connects to NetBox, retrieves hosts, and iterates through backups. |

---

## Example Usage Scenario

You can use this script to automatically back up all systems labeled with a tag like `backup_daily`, `backup_weekly`, or `critical` in NetBox.  
For example, set a device tag in NetBox, then configure this script’s `TAG = "backup_daily"` to include those systems in the nightly run.

---

## License

This script is provided under the MIT License.  
Use and modify freely for operational and automation purposes within your environment.
