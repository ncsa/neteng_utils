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
SSH_KEY_PATH = Path("<PATH_TO_KEY>")

# Juniper interface name prefixes to ignore
IGNORE_INTERFACE_PREFIXES = [
    "ipip",
    "dsc",
    "gre",
    "lsi",
    "mtun",
    "pimd",
    "pime",
    "tap",
    "fti0",
    "bme0",
    "jsrv",
    "irb",
    "gr-",
    "pfh",
    "sxe"

]

# Regex patterns to ignore full or wildcarded interfaces (e.g., vcp-0)
IGNORE_INTERFACE_REGEXES = [
    r"^vcp-",
    r"^vlan*",

]

# =================== ARGUMENT PARSING ===================
parser = argparse.ArgumentParser(description="Sync Juniper interface descriptions and speeds to NetBox.")
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
        "junos": "juniper",
    }
    if platform not in platform_map:
        raise ValueError(f"Unsupported NetBox platform.slug: '{platform}'")
    return platform_map[platform]

def should_ignore_interface(name):
    name = name.lower()
    if name.startswith(("irb.", "vlan.")):
        return False
    if any(name.startswith(pfx) for pfx in IGNORE_INTERFACE_PREFIXES):
        return True
    for pattern in IGNORE_INTERFACE_REGEXES:
        if re.match(pattern, name):
            return True
    return False

def guess_interface_type(speed_str, iface_name=""):
    speed_str = (speed_str or "").upper().strip()
    iface_name = iface_name.lower().strip()

    if iface_name.startswith(("irb.", "vlan.")):
        return "virtual"
    if "100000" in speed_str or "100G" in speed_str:
        return "100gbase-x-qsfp28"
    elif "40000" in speed_str or "40G" in speed_str:
        return "40gbase-x-qsfpp"
    elif "25000" in speed_str or "25G" in speed_str:
        return "25gbase-x-sfp28"
    elif "10000" in speed_str or "10G" in speed_str:
        return "10gbase-x-sfpp"
    elif "1000" in speed_str:
        return "1000base-t"
    elif "100" in speed_str:
        return "100base-tx"
    elif iface_name.startswith("et"):
        return "100gbase-x-qsfp28"
    elif iface_name.startswith("mge"):
        return "10gbase-t"
    elif iface_name.startswith("xe"):
        return "10gbase-x-sfpp"
    elif iface_name.startswith("ge"):
        return "1000base-t"
    elif iface_name.startswith("ae"):
        return "lag"
    elif iface_name.startswith(("lo", "vme")):
        return "1000base-t"
    return "other"

def convert_speed_to_int(speed_str):
    match = re.search(r"(\d+)", speed_str)
    if not match:
        return None
    val = int(match.group(1))
    if "G" in speed_str.upper() or val >= 10000:
        return val * 1_000_000
    elif "M" in speed_str.upper() or val >= 10:
        return val * 1_000
    return val

def parse_juniper_descriptions(output):
    descs = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) == 4 and parts[0] != "Interface":
            iface, admin, link, desc = parts
            descs[iface] = desc.strip('"')
    return descs

def parse_juniper_media(output):
    interfaces = {}
    current_iface = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Physical interface:"):
            match = re.match(r"Physical interface: (\S+),", line)
            if match:
                current_iface = match.group(1)
                interfaces[current_iface] = {}
        elif current_iface:
            match = re.search(r"Local link Speed: (\d+)\s*Mbps", line)
            if match:
                interfaces[current_iface]["speed"] = f"{match.group(1)}Mbps"
    return interfaces

# =================== MAIN ===================
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

devices = [
    d for d in nb.dcim.devices.filter(role=DEVICE_ROLE_SLUG, status="active")
    if d.primary_ip4 or d.primary_ip6
]

if args.device:
    devices = [d for d in devices if d.name == args.device]
    if not devices:
        logging.error(f"Device '{args.device}' not found or does not meet filter criteria.")
        exit(1)

for device in devices:
    logging.info(f"Processing device: {device.name}")
    ip_address = (device.primary_ip4 or device.primary_ip6).address.split("/")[0]

    if not device.platform:
        logging.error(f"{device.name} has no platform defined.")
        continue

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

        media_output = net_connect.send_command("show interfaces media")
        desc_output = net_connect.send_command("show interfaces descriptions")

        media_map = parse_juniper_media(media_output)
        desc_map = parse_juniper_descriptions(desc_output)

        existing = {
            i.name: i
            for i in nb.dcim.interfaces.filter(device_id=device.id)
        }

        for iface in desc_map.keys():
            if should_ignore_interface(iface):
                logging.info(f"Skipping interface {iface} on {device.name} due to ignore rule")
                continue

            raw_desc = desc_map.get(iface, "").strip()
            iface_description = re.sub(r"\s+", "_", raw_desc.upper())
            speed_str = media_map.get(iface, {}).get("speed", "")
            iface_speed = convert_speed_to_int(speed_str)
            iface_type = guess_interface_type(speed_str, iface)

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
