#!/usr/bin/env python3
"""
Arista EOS + Junos → NetBox: Learned MAC table sync
- Adds/removes learned MACs per interface; leaves other fields alone.
- LAG-aware: Arista Po*/Port-Channel*, Junos ae*.
- Reconciles *every* interface on the device (cleans NetBox when zero learned).
- Skips any interface whose NetBox description matches wildcard patterns (e.g., "[UP]*", and "[SW]*").
- Primary MAC hygiene:
    • Clear if current primary not in learned set
    • If none and exactly one learned remains, set it as primary

Usage:
  ./mac_sync.py                    # all active devices with role
  ./mac_sync.py -d <device_name>   # single device by exact NetBox device.name
"""

import sys
import re
import argparse
import logging
from typing import Dict, Any, Optional, List, Set, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pynetbox
from netmiko import ConnectHandler
from netmiko.ssh_exception import NetMikoTimeoutException, NetMikoAuthenticationException


# =================== CONFIGURATION ===================
NETBOX_URL = "<URL>"
NETBOX_TOKEN = "<TOKEN>"
DEVICE_ROLE_SLUG = "<ROLE>"
SSH_USERNAME = "<USERNAME>"
SSH_KEY_PATH = Path("PATH_TO_KEY")
WORKERS = 6

# Ignore NetBox interface *names* starting with any of these (not LAGs)
IGNORE_INTERFACE_PREFIXES: List[str] = [
    "irb", "vlan", "lo", "mgmt", "loopback",
]

# Ignore any NetBox interface whose *description* matches any of these wildcard patterns (case-insensitive).
# Wildcards: * (any chars), ? (single char). Brackets [] are treated literally.
# Example: "[UP]*" matches "[UP]", "[UP]_uplink_to_core", etc.
IGNORE_DESC_WILDCARDS = ["[UP]*", "[SW]*"]


# =================== LOGGING ===================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("mac_sync")


# =================== UTILS ===================

def mac_uc_colon(s: Optional[str]) -> Optional[str]:
    """Upper-case colon format: '68:8B:F4:30:45:90'."""
    if not s:
        return None
    hex_only = re.sub(r"[^0-9A-Fa-f]", "", s)
    if len(hex_only) != 12:
        return None
    return ":".join(hex_only[i:i+2] for i in range(0, 12, 2)).upper()

def should_skip_name(name: str) -> bool:
    n = (name or "").lower()
    return any(n.startswith(pfx) for pfx in IGNORE_INTERFACE_PREFIXES)

def map_platform_to_netmiko(platform_slug: str) -> Optional[str]:
    s = (platform_slug or "").lower()
    if not s:
        return None
    if "junos" in s or "juniper" in s:
        return "juniper"
    if "eos" in s or "arista" in s:
        return "arista_eos"
    return None

def get_device_mgmt_ip(dev) -> Optional[str]:
    ip_obj = getattr(dev, "primary_ip4", None) or getattr(dev, "primary_ip6", None)
    if not ip_obj or not getattr(ip_obj, "address", None):
        return None
    return ip_obj.address.split("/")[0]

_PO_RE = re.compile(r"^(?:po|port-channel)(\d+(?:/\d+)*)$", re.IGNORECASE)

def normalize_iface_name_for_netbox(device_type: str, ifname: str) -> str:
    """Arista: Ethernet2/1 → Et2/1 ; Port-Channel302/Po302 → Po302
       Junos:  collapse .unit: ge-0/0/0.0 → ge-0/0/0 ; ae3.0 → ae3
    """
    s = (ifname or "").strip()
    if device_type == "arista_eos":
        if s.lower().startswith("ethernet"):
            return "Et" + s[len("Ethernet"):]
        m = _PO_RE.match(s)
        if m:
            return f"Po{m.group(1)}"
        return s
    if device_type == "juniper":
        if "." in s:
            return s.split(".", 1)[0]
        return s
    return s

def canonical_nb_key(device_type: str, nb_iface_name: str) -> str:
    """Canonicalize NB interface name to match keys produced by normalize_iface_name_for_netbox."""
    s = (nb_iface_name or "").strip()
    if device_type == "arista_eos":
        m = _PO_RE.match(s)
        if m:
            return f"Po{m.group(1)}"
        return s
    if device_type == "juniper":
        if "." in s:
            return s.split(".", 1)[0]
        return s
    return s

def _wildcard_to_regex(pattern: str) -> str:
    # Treat * and ? as wildcards; everything else literal (incl. square brackets)
    rx = re.escape(pattern)
    rx = rx.replace(r"\*", ".*").replace(r"\?", ".")
    return rx

def has_ignored_desc(nb_iface) -> bool:
    desc = (getattr(nb_iface, "description", "") or "")
    for pat in IGNORE_DESC_WILDCARDS:
        rx = _wildcard_to_regex(pat)
        if re.search(rx, desc, re.IGNORECASE):
            return True
    return False


# =================== PARSERS (LEARNED MACS) ===================

# Accept Et*/Ethernet*, Po*, Port-Channel* as a valid "Port" token anywhere on the line.
ARISTA_PORT_TOKEN = r"(?:Et(?:hernet)?\d+(?:/\d+){0,2}|Po\d+(?:/\d+)*|Port-Channel\d+(?:/\d+)*)"

# Looser line regex: MAC, VLAN, Type, then capture the *next token* (Port),
# but DO NOT anchor to end-of-line, since Arista often has more columns (e.g., age/moves).
ARISTA_LINE_RE = re.compile(
    r"^\s*([0-9A-Fa-f:\.]{12,17})\s+\S+\s+\S+\s+(" + ARISTA_PORT_TOKEN + r")",
    re.IGNORECASE,
)

def arista_learned_macs(net_connect) -> Dict[str, Set[str]]:
    """
    Return { 'Ethernet2/1' or 'Et2/1' or 'Po302': {'AA:BB:..', ...}, ... }
    """
    try:
        net_connect.send_command("terminal length 0")
    except Exception:
        pass

    txt = net_connect.send_command("show mac address-table")
    per_if: Dict[str, Set[str]] = {}

    for raw in txt.splitlines():
        line = raw.strip()
        if not line or "-------" in line:
            continue
        if line.lower().startswith(("mac", "total", "multicast", "unicast", "static", "dynamic", "learned")):
            continue

        mac = None
        port = None

        # Preferred parse: MAC, VLAN, Type, Port (Port can be Et/Ethernet/Po/Port-Channel)
        m = ARISTA_LINE_RE.match(line)
        if m:
            mac = mac_uc_colon(m.group(1))
            port = m.group(2)
        else:
            # Fallback: find first MAC and any valid port token anywhere in the line
            mac_m = re.search(r"([0-9A-Fa-f:\.]{12,17})", line)
            port_m = re.search(ARISTA_PORT_TOKEN, line, re.IGNORECASE)
            mac = mac_uc_colon(mac_m.group(1)) if mac_m else None
            port = port_m.group(0) if port_m else None

        if not mac or not port:
            continue

        per_if.setdefault(port, set()).add(mac)

    return per_if


# Junos example:
# MAC                 VLAN     Logical interface
# 00:11:22:33:44:55  100      ge-0/0/1.0
JUNOS_LINE_RE = re.compile(r"^\s*([0-9A-Fa-f:\.]{12,17})\s+\S+\s+(\S+)\s+")

def junos_learned_macs(net_connect) -> Dict[str, Set[str]]:
    """
    Return { 'ge-0/0/0.0': {'AA:BB:..', ...}, 'ae3.0': {...}, ... }
    Caller will collapse .unit.
    """
    try:
        net_connect.send_command("set cli screen-length 0")
        net_connect.send_command("set cli screen-width 0")
    except Exception:
        pass

    txt = net_connect.send_command("show ethernet-switching table | no-more")
    per_if: Dict[str, Set[str]] = {}

    for line in txt.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("mac", "routing", "vlan", "interface", "count")):
            continue

        m = JUNOS_LINE_RE.match(line)
        if m:
            mac = mac_uc_colon(m.group(1))
            ifn = m.group(2)
        else:
            mac_m = re.search(r"([0-9A-Fa-f:\.]{12,17})", line)
            ifname_m = re.search(r"((?:ge|xe|et)-\d+/\d+/\d+(?:\.\d+)?|ae\d+(?:\.\d+)?)", line, re.IGNORECASE)
            mac = mac_uc_colon(mac_m.group(1)) if mac_m else None
            ifn = ifname_m.group(1) if ifname_m else None

        if not mac or not ifn:
            continue
        if should_skip_name(ifn):
            continue

        per_if.setdefault(ifn, set()).add(mac)

    return per_if


# =================== RAW NETBOX REST HELPERS ===================

def _headers():
    return {
        "Authorization": f"Token {NETBOX_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def _paged_get(url: str, params: Optional[dict]) -> List[dict]:
    items: List[dict] = []
    next_url = url
    next_params = params
    while True:
        r = requests.get(next_url, headers=_headers(), params=next_params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"GET failed: {r.status_code} {r.text}")
        data = r.json()
        if isinstance(data, dict) and "results" in data:
            items.extend(data.get("results") or [])
            next_url = data.get("next")
            next_params = None
            if not next_url:
                break
        else:
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict):
                items.append(data)
            break
    return items

def rest_list_interface_macs(iface_id: int) -> List[Dict[str, Any]]:
    """List MAC objects assigned to this interface (paged; robust filter)."""
    try:
        items = _paged_get(f"{NETBOX_URL}/api/dcim/mac-addresses/", {"assigned_object_id": iface_id})
        return [o for o in items if o.get("assigned_object_type") == "dcim.interface" and o.get("assigned_object_id") == iface_id]
    except Exception:
        items = _paged_get(f"{NETBOX_URL}/api/dcim/mac-addresses/", None)
        return [o for o in items if o.get("assigned_object_type") == "dcim.interface" and o.get("assigned_object_id") == iface_id]

def rest_create_mac(mac_str_uc: str, iface_id: int) -> int:
    payload = {
        "mac_address": mac_str_uc,
        "assigned_object_type": "dcim.interface",
        "assigned_object_id": iface_id,
    }
    r = requests.post(f"{NETBOX_URL}/api/dcim/mac-addresses/", headers=_headers(), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"POST mac-addresses failed: {r.status_code} {r.text}")
    return r.json()["id"]

def rest_delete_mac(mac_id: int) -> None:
    r = requests.delete(f"{NETBOX_URL}/api/dcim/mac-addresses/{mac_id}/", headers=_headers(), timeout=30)
    if r.status_code not in (204, 202):
        raise RuntimeError(f"DELETE mac-addresses/{mac_id} failed: {r.status_code} {r.text}")

def rest_patch_interface_primary_mac(iface_id: int, mac_obj_id: Optional[int]) -> None:
    payload = {"primary_mac_address": mac_obj_id}
    r = requests.patch(f"{NETBOX_URL}/api/dcim/interfaces/{iface_id}/", headers=_headers(), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"PATCH interface {iface_id} failed: {r.status_code} {r.text}")


# =================== SYNC LOGIC ===================

def fetch_nb_ifaces(nb, device_id: int) -> List[Any]:
    return list(nb.dcim.interfaces.filter(device_id=device_id))

def compute_changes(desired: Set[str], existing_objs: List[Dict[str, Any]]) -> Tuple[Set[str], List[Dict[str, Any]]]:
    existing_vals = {o.get("mac_address") for o in existing_objs if o.get("mac_address")}
    to_add = desired - existing_vals
    to_remove = [o for o in existing_objs if o.get("mac_address") not in desired]
    return to_add, to_remove

def sync_interface_macs(nb_iface, desired_macs_uc: Set[str]) -> Tuple[int, int]:
    """
    Ensure NetBox MACs for this interface match desired set.
    Returns (adds, removes).
    """
    iface_id = nb_iface.id
    existing = rest_list_interface_macs(iface_id)
    to_add, to_remove = compute_changes(desired_macs_uc, existing)

    adds = 0
    removes = 0

    # Remove stale first
    for obj in to_remove:
        try:
            rest_delete_mac(obj["id"])
            removes += 1
        except Exception as e:
            log.error(f"[{nb_iface.device.name}] DELETE MAC {obj.get('mac_address')} on {nb_iface.name} failed: {e}")

    # Add missing
    for mac in sorted(to_add):
        try:
            rest_create_mac(mac, iface_id)
            adds += 1
        except Exception as e:
            log.error(f"[{nb_iface.device.name}] CREATE MAC {mac} on {nb_iface.name} failed: {e}")

    # Refresh list to know final IDs
    final_objs = rest_list_interface_macs(iface_id)
    final_map = {o["mac_address"]: o["id"] for o in final_objs if "mac_address" in o and "id" in o}

    # Primary MAC hygiene
    try:
        current_primary = getattr(nb_iface, "primary_mac_address", None) or getattr(nb_iface, "mac_address", None)
        current_primary = mac_uc_colon(current_primary) if current_primary else None
    except Exception:
        current_primary = None

    if current_primary and current_primary not in desired_macs_uc:
        try:
            rest_patch_interface_primary_mac(iface_id, None)
        except Exception as e:
            log.error(f"[{nb_iface.device.name}] Clear primary on {nb_iface.name} failed: {e}")

    if not current_primary and len(desired_macs_uc) == 1:
        only_mac = next(iter(desired_macs_uc))
        only_id = final_map.get(only_mac)
        if only_id:
            try:
                rest_patch_interface_primary_mac(iface_id, only_id)
            except Exception as e:
                log.error(f"[{nb_iface.device.name}] Set primary to {only_mac} on {nb_iface.name} failed: {e}")

    return adds, removes


# =================== SYNC ONE DEVICE ===================

def sync_one(nb, device) -> Dict[str, Any]:
    name = device.name
    summary = {"device": name, "adds": 0, "removes": 0, "errors": []}

    ip = get_device_mgmt_ip(device)
    if not ip:
        summary["errors"].append("No primary IP")
        return summary

    platform_slug = getattr(getattr(device, "platform", None), "slug", None)
    device_type = map_platform_to_netmiko(platform_slug)
    if not device_type:
        log.info(f"[{name}] Skipping (unsupported platform '{platform_slug}')")
        return summary

    log.info(f"[{name}] Connecting to {ip} (platform={platform_slug})")

    conn_params = {
        "device_type": device_type,
        "host": ip,
        "username": SSH_USERNAME,
        "use_keys": True,
        "key_file": str(SSH_KEY_PATH),
    }

    # Gather learned MACs
    try:
        net_connect = ConnectHandler(**conn_params)

        if device_type == "arista_eos":
            learned_raw = arista_learned_macs(net_connect)  # keys like 'Et..'/'Ethernet..', 'Po..', 'Port-Channel..'
        elif device_type == "juniper":
            learned_raw = junos_learned_macs(net_connect)   # keys like 'ge-.. .unit', 'ae.. .unit'
        else:
            net_connect.disconnect()
            log.info(f"[{name}] Skipping (unsupported device_type '{device_type}')")
            return summary

        net_connect.disconnect()

    except (NetMikoTimeoutException, NetMikoAuthenticationException) as e:
        err = f"SSH failed: {e}"
        log.error(f"[{name}] {err}")
        summary["errors"].append(err)
        return summary
    except Exception as e:
        err = f"Gather error: {e}"
        log.error(f"[{name}] {err}")
        summary["errors"].append(err)
        return summary

    # Normalize learned keys to NB canonical keys
    learned_norm: Dict[str, Set[str]] = {}
    for raw_ifname, macs in learned_raw.items():
        key = normalize_iface_name_for_netbox(device_type, raw_ifname)
        learned_norm.setdefault(key, set()).update(macs)

    # Fetch NB interfaces and reconcile ALL of them
    nb_ifaces_list = fetch_nb_ifaces(nb, device.id)
    total_adds = 0
    total_removes = 0

    for nb_iface in nb_ifaces_list:
        # Name-based skips (IRB/VLAN/mgmt/etc.)
        if should_skip_name(nb_iface.name):
            continue
        # Description-based wildcard ignore
        if has_ignored_desc(nb_iface):
            log.info(f"[{name}] skipping {nb_iface.name} (desc matches IGNORE_DESC_WILDCARDS)")
            continue

        # Canonical key for this NB iface
        key = canonical_nb_key(device_type, nb_iface.name)
        desired = learned_norm.get(key, set())

        try:
            adds, removes = sync_interface_macs(nb_iface, set(desired))
            total_adds += adds
            total_removes += removes
            if adds or removes:
                log.info(f"[{name}] {nb_iface.name}: +{adds} / -{removes} (desired={len(desired)})")
        except Exception as e:
            msg = f"NetBox write failed on {nb_iface.name}: {e}"
            log.error(f"[{name}] {msg}")
            summary["errors"].append(msg)

    summary["adds"] = total_adds
    summary["removes"] = total_removes
    return summary


# =================== MAIN ===================

def main(args):
    if not SSH_KEY_PATH.exists():
        log.error(f"SSH key not found: {SSH_KEY_PATH}")
        sys.exit(2)

    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

    devices = [
        d for d in nb.dcim.devices.filter(role=DEVICE_ROLE_SLUG, status="active")
        if getattr(d, "primary_ip4", None) or getattr(d, "primary_ip6", None)
    ]

    if args.device:
        devices = [d for d in devices if d.name == args.device]
        if not devices:
            log.error(f"Device '{args.device}' not found (or no primary IP / inactive).")
            sys.exit(1)
        log.info(f"Limiting to device: {args.device}")

    if not devices:
        log.error("No eligible devices found (role=status:active, has primary IP).")
        sys.exit(1)

    summaries = []
    with ThreadPoolExecutor(max_workers=WORKERS) as tp:
        futures = [tp.submit(sync_one, nb, d) for d in devices]
        for f in as_completed(futures):
            summaries.append(f.result())

    total_adds = sum(s["adds"] for s in summaries)
    total_removes = sum(s["removes"] for s in summaries)  # <-- fixed
    total_errors = sum(len(s["errors"]) for s in summaries)

    for s in summaries:
        log.info(f"{s['device']}: +{s['adds']} / -{s['removes']} errors={len(s['errors'])}")
        for e in s["errors"]:
            log.error(f"  - {e}")

    log.info(f"TOTAL: adds={total_adds} removes={total_removes} errors={total_errors}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sync learned MAC addresses per interface into NetBox.")
    ap.add_argument("-d", "--device", help="Limit to a single device name (exact NetBox device.name)")
    args = ap.parse_args()
    main(args)
