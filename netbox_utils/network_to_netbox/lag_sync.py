#!/usr/bin/env python3
"""
Device → NetBox LAG membership sync (update-only)

- Juniper: parses `show configuration interfaces | display set | match "802.3ad ae"`
  to map physical members (ge-/xe-/et- ports) → LAG (aeX)
- Arista EOS: parses `show port-channel summary` to map members (EthernetX) → LAG (PoN)
- Updates NetBox member interface 'lag' to point at the correct LAG interface
- Assumes Arista LAG interfaces in NetBox are named exactly 'PoN' (e.g., Po1)

Requirements:
  pip install pynetbox netmiko paramiko requests
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict

import pynetbox
from netmiko import ConnectHandler
from netmiko.ssh_exception import NetMikoAuthenticationException, NetMikoTimeoutException

# =================== CONFIG (env-compatible like your VLAN script) ===================

NETBOX_URL = os.environ.get("NETBOX_URL", "<URL>")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "<TOKEN>")
DEVICE_ROLE_SLUG = os.environ.get("NETBOX_ROLE", "<ROLE>")

SSH_USERNAME = os.environ.get("NB_SSH_USER", "<USERNAME>")
# Keep the same hardcoded key path you used:
SSH_KEY_PATH = Path("<PATH_TO_KEY>")

# =================== ARGS & LOGGING ===================

ap = argparse.ArgumentParser(description="Device→NetBox LAG member assignment sync.")
ap.add_argument("-d", "--device", help="Limit to a single device name (exact NetBox device.name)")
ap.add_argument("--dry-run", action="store_true", help="Only show what would change; do not write NetBox")
args = ap.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("lag-sync")

# =================== Helpers ===================

def map_platform_to_netmiko(platform_slug: str) -> str:
    s = (platform_slug or "").lower()
    if not s:
        raise ValueError("Device platform.slug is missing")
    if "junos" in s or "juniper" in s:
        return "juniper"          # match your working script
    if "eos" in s or "arista" in s:
        return "arista_eos"       # match your working script
    raise ValueError(f"Unsupported NetBox platform.slug: '{platform_slug}'")

def normalize_arista_ifname(name: str) -> str:
    """Normalize short EOS names to NetBox style (EthernetX)."""
    name = name.strip()
    name = re.sub(r'^Et', 'Ethernet', name)
    name = re.sub(r'^Eth', 'Ethernet', name)
    return name

def fetch_iface_map(nb, device_id: int) -> Dict[str, object]:
    """Return name -> interface object map for this device."""
    return {i.name: i for i in nb.dcim.interfaces.filter(device_id=device_id)}

# =================== Discovery (Junos & EOS) ===================

# set interfaces et-0/0/0 ether-options 802.3ad ae2
AE_RE = re.compile(r'^set\s+interfaces\s+(\S+)\s+.*802\.3ad\s+(ae\d+)\s*$')

def discover_member_to_lag_junos(net_connect) -> Dict[str, str]:
    """
    Returns { member_if : aeX } using 'display set' config.
    """
    text = net_connect.send_command('show configuration interfaces | display set | match "802.3ad ae"')
    mapping: Dict[str, str] = {}
    for line in text.splitlines():
        m = AE_RE.match(line.strip())
        if not m:
            continue
        member, lag = m.group(1), m.group(2)
        member = member.split(".")[0]  # ensure physical
        mapping[member] = lag
    return mapping

# Example EOS lines (varies by version):
# "1   Po1(SU)   LACP   LACP   Et1(P) Et2(P)"
ARISTA_SUMMARY_LINE = re.compile(r'^\s*(\d+)\s+Po(\d+)\([A-Za-z]+\)\s+\S+\s+(.*)$')
ARISTA_MEMBER_TOKEN  = re.compile(r'([A-Za-z]+[\d/]+)\([A-Za-z]+\)')

def discover_member_to_lag_eos(net_connect) -> Dict[str, str]:
    """
    Returns { member_if : PoN } using 'show port-channel summary'.
    Assumes NetBox LAG names are PoN.
    """
    text = net_connect.send_command("show port-channel summary")
    mapping: Dict[str, str] = {}
    for line in text.splitlines():
        mm = ARISTA_SUMMARY_LINE.match(line.rstrip())
        if not mm:
            continue
        _grp, po_num, members_blob = mm.groups()
        lag_name = f"Po{po_num}"
        for t in ARISTA_MEMBER_TOKEN.findall(members_blob):
            member = normalize_arista_ifname(t)
            mapping[member] = lag_name
    return mapping

# =================== Main ===================

def main():
    if not NETBOX_TOKEN or NETBOX_TOKEN.startswith("REPLACE_"):
        log.error("Set NETBOX_TOKEN in env or edit script.")
        sys.exit(2)

    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

    # Only active devices with a primary IP (v4 or v6), like your VLAN script
    devices = [
        d for d in nb.dcim.devices.filter(role=DEVICE_ROLE_SLUG, status="active")
        if d.primary_ip4 or d.primary_ip6
    ]
    if args.device:
        devices = [d for d in devices if d.name == args.device]
        if not devices:
            log.error(f"Device '{args.device}' not found or no primary IP.")
            sys.exit(1)

    total_changed = 0
    total_seen = 0

    for device in devices:
        name = device.name
        if not device.platform:
            log.warning(f"{name} has no platform defined; skipping")
            continue

        try:
            device_type = map_platform_to_netmiko(device.platform.slug)
        except ValueError as e:
            log.warning(f"{name}: {e}; skipping")
            continue

        ip_obj = device.primary_ip4 or device.primary_ip6
        ip_address = ip_obj.address.split("/")[0] if ip_obj else None
        if not ip_address:
            log.warning(f"{name}: no primary IP; skipping")
            continue

        log.info(f"[{name}] Connecting {ip_address} as {SSH_USERNAME} with key {SSH_KEY_PATH}")

        conn_params = {
            "device_type": device_type,
            "host": ip_address,
            "username": SSH_USERNAME,
            "use_keys": True,
            "key_file": str(SSH_KEY_PATH),
        }

        try:
            net_connect = ConnectHandler(**conn_params)

            if device_type == "juniper":
                member_map = discover_member_to_lag_junos(net_connect)
            elif device_type == "arista_eos":
                member_map = discover_member_to_lag_eos(net_connect)
            else:
                log.warning(f"[{name}] Unsupported mapped device_type {device_type}; skipping")
                net_connect.disconnect()
                continue

            net_connect.disconnect()

            if not member_map:
                log.info(f"[{name}] No member→LAG mappings discovered; skipping")
                continue

            iface_map = fetch_iface_map(nb, device.id)

            for member_name, lag_name in sorted(member_map.items()):
                total_seen += 1

                member = iface_map.get(member_name)
                lag_iface = iface_map.get(lag_name)  # Junos aeX or Arista PoN

                if not member:
                    log.info(f"[{name}] skip {member_name}: not present in NetBox")
                    continue
                if not lag_iface:
                    log.info(f"[{name}] skip {member_name}: LAG {lag_name} not present in NetBox")
                    continue

                current = getattr(member, "lag", None)
                if current and current.id == lag_iface.id:
                    continue  # already correct

                if args.dry_run:
                    log.info(f"[DRY-RUN][{name}] {member.name} -> {lag_iface.name}")
                else:
                    try:
                        member.update({"lag": lag_iface.id})
                        log.info(f"[{name}] {member.name} -> {lag_iface.name}")
                        total_changed += 1
                    except pynetbox.RequestError as e:
                        log.error(f"[{name}] NetBox update error on {member.name}: {e.error}")

        except (NetMikoTimeoutException, NetMikoAuthenticationException) as e:
            log.error(f"[{name}] SSH failed: {e}")
            continue
        except Exception as e:
            log.error(f"[{name}] unhandled error: {e}")
            continue

    log.info(f"SUMMARY: candidates_seen={total_seen}, netbox_changes={total_changed}")

if __name__ == "__main__":
    main()
