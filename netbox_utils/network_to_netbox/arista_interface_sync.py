#!/usr/bin/env python3

import logging
import re
import io
import argparse
from pathlib import Path
import pynetbox
from netmiko import ConnectHandler
from netmiko.ssh_exception import NetMikoTimeoutException, NetMikoAuthenticationException
from paramiko import Ed25519Key, RSAKey

# =================== CONFIGURATION ===================
NETBOX_URL = "<URL>"
NETBOX_TOKEN = "<TOKEN>"
DEVICE_ROLE_SLUG = "network_device"
SSH_USERNAME = "<USERNAME>"
SSH_KEY_PATH = Path("<KEY>")

# =================== ARGUMENT PARSING ===================
parser = argparse.ArgumentParser(description="Sync interface descriptions and speeds to NetBox.")
parser.add_argument("-d", "--device", help="Specify a single device name to sync")
parser.add_argument("--dry-run", action="store_true", help="Only show what would be changed")
args = parser.parse_args()

# =================== LOGGING ===================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =================== SSH KEY ===================
def load_private_key(path):
    try:
        with open(path, "r") as f:
            key_data = f.read()
        return Ed25519Key(file_obj=io.StringIO(key_data))
    except Exception:
        with open(path, "r") as f:
            key_data = f.read()
        return RSAKey(file_obj=io.StringIO(key_data))

# =================== HELPERS ===================
def map_platform_to_netmiko(platform):
    platform_map = {
        "eos": "arista_eos",
    }
    if platform not in platform_map:
        raise ValueError(f"Unsupported NetBox platform.slug: '{platform}'")
    return platform_map[platform]

def guess_interface_type(type_str, speed_str, iface_name=""):
    iface_name = iface_name.lower()
    norm_type = (type_str or "").strip().upper()
    norm_speed = (speed_str or "").strip().upper()

    if iface_name.startswith(("vlan", "vl")):
        return "virtual"
    if iface_name.startswith(("po")):
        return "lag"

    source = norm_speed if norm_type in ["NOT", "NOT PRESENT", "NOTPRESENT", ""] else norm_type

    if "100M" in source:
        return "100base-tx"
    elif "1G" in source or "1000M" in source or "1000BASE-T" in source or "10/100/1000" in source:
        return "1000base-t"
    elif "10G" in source:
        return "10gbase-x-sfpp"
    elif "25G" in source:
        return "25gbase-x-sfp28"
    elif "40G" in source:
        return "40gbase-x-qsfpp"
    elif "100G" in source:
        return "100gbase-x-qsfp28"
    elif "400G" in source or "400GBASE" in source:
        return "400gbase-x-qsfpdd"

    return "other"

def convert_speed_to_int(speed_str):
    match = re.match(r"(\d+)([GM])", speed_str.upper())
    if not match:
        return None
    val, unit = match.groups()
    return int(val) * (1_000_000 if unit == "G" else 1_000)

def parse_arista_status(output):
    status_data = {}
    for line in output.splitlines():
        if not line.strip() or line.strip().startswith("Port"):
            continue
        iface = line[0:9].strip()
        speed = line[55:64].strip()
        type_ = line[64:].strip()
        status_data[iface] = {"speed": speed, "type": type_}
    return status_data

def parse_arista_description(output):
    descs = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        if line.strip().startswith("Interface"):  # Skip header
            continue
        parts = line.split(None, 3)
        iface = parts[0]
        desc = parts[3].strip() if len(parts) == 4 else ""
        descs[iface] = desc
    return descs

# =================== MAIN ===================
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
devices = nb.dcim.devices.filter(role=DEVICE_ROLE_SLUG)

if args.device:
    devices = [d for d in devices if d.name == args.device]
    if not devices:
        logging.error(f"Device '{args.device}' not found.")
        exit(1)

for device in devices:
    logging.info(f"Processing device: {device.name}")
    if not device.primary_ip4 and not device.primary_ip6:
        logging.warning(f"{device.name} has no primary IP assigned.")
        continue
    if not device.platform:
        logging.error(f"{device.name} has no platform.")
        continue

    ip_address = (device.primary_ip4 or device.primary_ip6).address.split("/")[0]
    try:
        device_type = map_platform_to_netmiko(device.platform.slug)
    except ValueError as e:
        logging.error(str(e))
        continue

    conn_params = {
        "device_type": device_type,
        "host": ip_address,
        "username": SSH_USERNAME,
        "use_keys": True,
        "key_file": str(SSH_KEY_PATH),
    }

    try:
        net_connect = ConnectHandler(**conn_params)

        if device.platform.slug == "eos":
            desc_output = net_connect.send_command("show interfaces description")
            status_output = net_connect.send_command("show interfaces status")
            desc_map = parse_arista_description(desc_output)
            status_map = parse_arista_status(status_output)
        else:
            logging.error(f"{device.name}: platform not supported for parsing.")
            continue

        existing = {
            i.name: i
            for i in nb.dcim.interfaces.filter(device_id=device.id)
        }

        all_ifaces = set(status_map) | set(desc_map)

        for iface in all_ifaces:
            status_info = status_map.get(iface, {})
            type_str = status_info.get("type", "")
            speed_str = status_info.get("speed", "")
            iface_type = guess_interface_type(type_str, speed_str, iface)
            iface_speed = convert_speed_to_int(speed_str)
            raw_desc = desc_map.get(iface, "").strip()
            iface_description = raw_desc.upper().replace(" ", "_")

            updated = {
                "type": iface_type,
                "description": iface_description,
                "speed": iface_speed,
            }

            if iface in existing:
                iface_obj = nb.dcim.interfaces.get(existing[iface].id)
                diff = {}

                for k, v in updated.items():
                    current_val = getattr(iface_obj, k, None)

                    if k == "description":
                        current_val = (current_val or "").strip()
                        v = (v or "").strip()
                        if current_val != v:
                            diff[k] = v
                    else:
                        if current_val != v:
                            diff[k] = v

                if diff:
                    logging.info(f"{'Would update' if args.dry_run else 'Updating'} {device.name} interface {iface}: {diff}")
                    if not args.dry_run:
                        try:
                            iface_obj.update(diff)
                        except pynetbox.RequestError as e:
                            logging.error(f"Failed to update interface {iface} on {device.name}: {e.error}")
            else:
                payload = {
                    "device": device.id,
                    "name": iface,
                    "type": iface_type,
                    "description": iface_description,
                    "speed": iface_speed,
                }
                logging.info(f"{'Would create' if args.dry_run else 'Creating'} {device.name} interface {iface}: {payload}")
                if not args.dry_run:
                    try:
                        nb.dcim.interfaces.create(payload)
                    except pynetbox.RequestError as e:
                        logging.error(f"Failed to create interface {iface} on {device.name}: {e.error}")

        net_connect.disconnect()

    except (NetMikoTimeoutException, NetMikoAuthenticationException) as e:
        logging.error(f"SSH failed for {device.name}: {e}")
    except Exception as e:
        logging.error(f"Unhandled error for {device.name}: {e}")
