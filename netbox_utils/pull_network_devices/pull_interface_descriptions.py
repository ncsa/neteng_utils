#!/usr/bin/env python3

import pynetbox
from netmiko import ConnectHandler
from pathlib import Path
import logging
import paramiko
import argparse
import re

# ========== CONFIGURATION ==========
NETBOX_URL = "<URL>"
NETBOX_TOKEN = "<TOKEN>"
DEVICE_ROLE_SLUG = "<ROLE>"
SSH_USERNAME = "<USERNAME>"
SSH_KEY_PATH = Path.home() / ".ssh" / "<KEY>"

# ========== LOGGING SETUP ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# ========== NETBOX ==========
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

# ========== PLATFORM MAP ==========
def map_platform_to_netmiko(platform):
    platform_map = {
        "ios": "cisco_ios",
        "eos": "arista_eos",
        "junos": "juniper",
        "asa": "cisco_asa",
    }
    return platform_map.get(platform, None)

# ========== FUNCTIONS ==========
def get_devices(role_slug, specific_device=None):
    if specific_device:
        logging.info(f"[INFO] Looking up specific device: {specific_device}")
        device = nb.dcim.devices.get(name=specific_device)
        return [device] if device else []
    logging.info(f"[INFO] Querying NetBox for devices with role '{role_slug}'")
    return [nb.dcim.devices.get(id=d.id) for d in nb.dcim.devices.filter(role=role_slug, status="active")]

def resolve_ip(ip_ref):
    if not ip_ref:
        return None
    if hasattr(ip_ref, "address"):
        return ip_ref.address.split("/")[0]
    if isinstance(ip_ref, str) and ip_ref.startswith("/api/ipam/ip-addresses/"):
        ip_id = ip_ref.strip("/").split("/")[-1]
        ip_obj = nb.ipam.ip_addresses.get(id=ip_id)
        if ip_obj and hasattr(ip_obj, "address"):
            return ip_obj.address.split("/")[0]
    return None

def get_primary_ip(device):
    return resolve_ip(device.primary_ip4) or resolve_ip(device.primary_ip6) or resolve_ip(device.primary_ip)

def get_interface_descriptions(device_type, conn):
    try:
        if device_type in ("cisco_ios", "arista_eos", "cisco_asa"):
            output = conn.send_command("show interfaces description")
        elif device_type == "juniper":
            output = conn.send_command("show interfaces descriptions | no-more")
        else:
            logging.warning(f"[SKIP] Unsupported device type: {device_type}")
            return {}

        if not output.strip():
            logging.warning("[WARN] Empty output from device.")
            return {}

        interface_map = {}
        for line in output.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("interface"):
                continue
            parts = line.split(None, 3)
            if len(parts) < 3:
                continue
            iface = parts[0]
            raw_desc = parts[3].strip() if len(parts) == 4 else ""
            sanitized_desc = re.sub(r"\s+", "_", raw_desc.upper())
            interface_map[iface] = sanitized_desc

        return interface_map
    except Exception as e:
        logging.error(f"[ERROR] Failed to parse interface descriptions: {e}")
        return {}

def update_netbox_interface_descriptions(device_name, interface_data):
    device = nb.dcim.devices.get(name=device_name)
    if not device:
        logging.error(f"[ERROR] Device '{device_name}' not found in NetBox.")
        return

    for iface_name, desc in interface_data.items():
        iface = nb.dcim.interfaces.get(device_id=device.id, name=iface_name)
        if not iface:
            logging.warning(f"[SKIP] Interface '{iface_name}' not found in NetBox for {device_name}")
            continue

        current_desc = iface.description or ""
        if current_desc.strip() != desc.strip():
            logging.info(f"[UPDATE] {device_name} - {iface.name}")
            logging.info(f"    OLD: '{current_desc}'")
            logging.info(f"    NEW: '{desc}'")
            try:
                iface.update({"description": desc})
                logging.info(f"[UPDATED] {device_name} - {iface.name}")
            except Exception as e:
                logging.error(f"[ERROR] Failed to update {device_name} - {iface.name}: {e}")
        else:
            logging.info(f"[MATCH] {device_name} - {iface.name} already correct")

# ========== MAIN ==========
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync NetBox interface descriptions from devices")
    parser.add_argument("-d", "--device", help="Run against a specific device (by name)")
    args = parser.parse_args()

    if not SSH_KEY_PATH.exists():
        logging.error(f"Private key not found: {SSH_KEY_PATH}")
        exit(1)

    pkey = paramiko.Ed25519Key(filename=str(SSH_KEY_PATH))
    devices = get_devices(DEVICE_ROLE_SLUG, args.device)

    if not devices:
        logging.error("[ERROR] No devices found.")
        exit(1)

    for device in devices:
        if not device:
            continue

        device_name = device.name
        platform_slug = device.platform.slug if device.platform else ""
        device_type = map_platform_to_netmiko(platform_slug)

        if not device_type:
            logging.warning(f"[SKIP] No valid platform for {device_name}")
            continue

        mgmt_ip = get_primary_ip(device)
        if not mgmt_ip:
            logging.warning(f"[SKIP] No primary IP for {device_name}")
            continue

        logging.info(f"[CONNECT] Connecting to {device_name} at {mgmt_ip} ({device_type})")

        try:
            conn = ConnectHandler(
                device_type=device_type,
                host=mgmt_ip,
                username=SSH_USERNAME,
                use_keys=False,
                allow_agent=False,
                pkey=pkey,
            )
            iface_descs = get_interface_descriptions(device_type, conn)
            conn.disconnect()

            if not iface_descs:
                logging.warning(f"[SKIP] No interface data from {device_name}")
                continue

            update_netbox_interface_descriptions(device_name, iface_descs)

        except Exception as e:
            logging.error(f"[ERROR] SSH to {device_name} failed: {e}")
