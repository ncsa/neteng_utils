#!/usr/bin/env python3

import os
import logging
import pynetbox

# ======================= CONFIGURATION =======================

API_URL = "<URL>"  # NetBox base URL (no trailing /api)
API_TOKEN = "<TOKEN>"  # NetBox API Token
TAG = "<TAG>"  # Tag to filter devices/VMs for backup
BACKUP_BASE_PATH = "/backup/path/dir" # Base directory where invidual host folders are created
EXCLUDE_FILE = "/backup/path/exclude" # File where you can put exclude information"
SSH_KEY = "/backup/path/key/.ssh/id_rsa" # Private key used for rsync backups

# ======================= LOGGING SETUP =======================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ======================= MAIN LOGIC ==========================

def get_online_entities_with_tag(nb, tag):
    """Fetch active devices and VMs with a specific tag from NetBox using pynetbox."""
    entities = []

    try:
        devices = nb.dcim.devices.filter(status='active', tag=tag)
        for device in devices:
            entities.append(device.name)
    except Exception as e:
        logging.error(f"Error fetching devices: {e}")

    try:
        vms = nb.virtualization.virtual_machines.filter(tag=tag)
        for vm in vms:
            if hasattr(vm, "status") and vm.status and vm.status.value == "active":
                entities.append(vm.name)
    except Exception as e:
        logging.error(f"Error fetching virtual machines: {e}")

    return entities

def backup_device(server):
    """Backs up a server using rsync and ZFS if necessary."""
    logging.info(f"########### Backing Up {server} #############")

    backup_path = os.path.join(BACKUP_BASE_PATH, server)

    rsync_base = f"""
        rsync -arv --delete \
            --exclude-from={EXCLUDE_FILE} \
            --rsync-path="sudo rsync" \
            -e "ssh -i {SSH_KEY}" \
            rsbackup@{server}:/ {backup_path}/
    """

    if os.path.exists(backup_path):
        logging.info(f"Backup path {backup_path} exists. Proceeding with rsync.")
        command = rsync_base
    else:
        logging.info(f"Backup path {backup_path} does not exist. Creating ZFS filesystem.")
        command = f"zfs create services/backup/servers/rsync_backup/{server} ; {rsync_base}"

    exit_code = os.system(command)
    if exit_code == 0:
        logging.info(f"Backup for {server} completed successfully.")
    else:
        logging.error(f"!!!!!!!! Backup for {server} failed with exit code {exit_code}. !!!!!!!!")

if __name__ == "__main__":
    nb = pynetbox.api(API_URL, token=API_TOKEN)
    online_entities = get_online_entities_with_tag(nb, TAG)

    for entity in online_entities:
        backup_device(entity)
