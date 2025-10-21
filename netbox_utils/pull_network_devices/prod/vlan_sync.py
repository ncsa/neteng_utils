#!/usr/bin/env python3
"""
Device → NetBox VLAN sync (update-only, no device pushes)

- Juniper: Uses *configuration* as source of truth
    * `show configuration vlans | display set`  -> build name→VID map (e.g., v2013 -> 2013)
    * `show configuration interfaces | display set` -> per-port L2 mode/membership/native
    * Collapse ge-*/xe-*/et-* units (.0) to physical names for NetBox match
- Arista EOS / Cisco IOS/NX-OS: parses switchport outputs
- Updates NetBox interface "mode", "untagged_vlan" (for access/native), and "tagged_vlans"
- Removes extra VLANs in NetBox that the device does not have
- Never pushes config to devices

Requirements:
  pip install pynetbox netmiko paramiko requests
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pynetbox
from netmiko import ConnectHandler
from netmiko.ssh_exception import NetMikoAuthenticationException, NetMikoTimeoutException

# =================== CONFIG (top) ===================

NETBOX_URL = os.environ.get("NETBOX_URL", "<URL>")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "<TOKEN>")
DEVICE_ROLE_SLUG = os.environ.get("NETBOX_ROLE", "<ROLE>")

SSH_USERNAME = os.environ.get("NB_SSH_USER", "<USERNAME>")
SSH_KEY_PATH = Path("<PATH_TO_KEY")


# Ignore lists
IGNORE_INTERFACE_PREFIXES = [
    "ipip", "dsc", "gre", "lsi", "mtun", "pimd", "pime", "tap",
    "fti0", "bme0", "jsrv", "vme", "irb",
]
IGNORE_INTERFACE_REGEXES = [
    r"^vlan.*",
    # r"^vcp-",
]

# =================== ARGS & LOGGING ===================

ap = argparse.ArgumentParser(description="Device→NetBox VLAN sync (update-only).")
ap.add_argument("-d", "--device", help="Limit to a single device name (exact NetBox device.name)")
ap.add_argument("--dry-run", action="store_true", help="Only show what would be changed; do not write NetBox")
args = ap.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =================== HELPERS ===================

def get_field(obj: Any, field: str, default: Any = None) -> Any:
    if obj is None:
        return default
    try:
        if isinstance(obj, dict):
            return obj.get(field, default)
        return getattr(obj, field)
    except Exception:
        return default

def map_platform_to_netmiko(platform_slug: str) -> str:
    s = (platform_slug or "").lower()
    if not s:
        raise ValueError("Device platform.slug is missing")
    if "junos" in s or "juniper" in s:
        return "juniper"
    if "eos" in s or "arista" in s:
        return "arista_eos"
    if "nx" in s or "nxos" in s:
        return "cisco_nxos"
    if "ios" in s or "cisco" in s:
        return "cisco_ios"
    raise ValueError(f"Unsupported NetBox platform.slug: '{platform_slug}'")

def should_ignore_interface(name: str) -> bool:
    name_l = (name or "").lower()
    if name_l.startswith(("irb.", "vlan.")):
        return False
    if any(name_l.startswith(pfx) for pfx in IGNORE_INTERFACE_PREFIXES):
        return True
    for pattern in IGNORE_INTERFACE_REGEXES:
        if re.match(pattern, name_l):
            return True
    return False

# ---------- NetBox VLAN lookups ----------

class VlanLookups:
    def __init__(self, vid_to_id: Dict[int, int]):
        self.vid_to_id = vid_to_id

    def nb_id_from_vid(self, vid: Optional[int]) -> Optional[int]:
        if vid is None:
            return None
        return self.vid_to_id.get(vid)

def nb_vlan_lookups(nb, site_obj: Any) -> VlanLookups:
    def collect(vlans) -> VlanLookups:
        v2i: Dict[int, int] = {}
        for v in vlans:
            vid = get_field(v, "vid")
            nbid = get_field(v, "id")
            try:
                vid = int(vid) if vid is not None else None
            except Exception:
                vid = None
            if nbid and vid is not None:
                v2i[vid] = nbid
        return VlanLookups(v2i)

    site_id = get_field(site_obj, "id")
    site_slug = get_field(site_obj, "slug")
    if site_id:
        lk = collect(nb.ipam.vlans.filter(site_id=site_id))
        if lk.vid_to_id:
            return lk
    if site_slug:
        lk = collect(nb.ipam.vlans.filter(site=site_slug))
        if lk.vid_to_id:
            return lk
    return collect(nb.ipam.vlans.all())

def parse_vlan_list_str(s: str) -> List[int]:
    """Parse '1-3,5,7-9' → [1,2,3,5,7,8,9]."""
    out: List[int] = []
    if not s:
        return out
    for part in s.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a, b = int(a), int(b)
                out.extend(range(a, b + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return sorted(set(out))

# =================== Cisco / Arista ===================

def parse_ios_switchport(text: str) -> Dict[str, dict]:
    """Cisco IOS/NX-OS 'show interface(s) switchport'."""
    result: Dict[str, dict] = {}
    blocks = re.split(r"\n(?=Name:\s)", text)
    for b in blocks:
        m = re.search(r"^Name:\s+(\S+)", b, re.MULTILINE)
        if not m:
            continue
        ifname = m.group(1)
        admin_mode = re.search(r"Administrative Mode:\s+(\S+)", b)
        oper_mode  = re.search(r"Operational Mode:\s+(\S+)", b)
        mode = (oper_mode.group(1) if oper_mode else (admin_mode.group(1) if admin_mode else "")).lower()
        acc = int(ma.group(1)) if (ma := re.search(r"Access Mode VLAN:\s+(\d+)", b)) else None
        native = int(mn.group(1)) if (mn := re.search(r"Trunking Native Mode VLAN:\s+(\d+)", b)) else None
        tagged = parse_vlan_list_str(re.search(r"Trunking VLANs Enabled:\s+([0-9,\- ]+)", b).group(1)) if re.search(r"Trunking VLANs Enabled:\s+([0-9,\- ]+)", b) else []
        if mode.startswith("trunk"):
            mode = "trunk"
        elif mode.startswith("access"):
            mode = "access"
        else:
            mode = "trunk" if tagged else ("access" if acc is not None else "")
        if not mode:
            continue
        result[ifname] = {"mode": mode, "access": acc, "native": native, "tagged": tagged}
    return result

def parse_eos_switchport(text: str) -> Dict[str, dict]:
    return parse_ios_switchport(text)

# =================== Junos (config-first) ===================

# set vlans v2013 vlan-id 2013
CFG_SET_VLAN_VID_RE = re.compile(r"^set\s+vlans\s+(\S+)\s+vlan-id\s+(\d+)\s*$")

# set interfaces ge-1/0/0 unit 0 family ethernet-switching <...>
CFG_SET_IF_RE = re.compile(
    r"^set\s+interfaces\s+(\S+)\s+unit\s+(\d+)\s+family\s+ethernet-switching\s+(.*)$"
)

def _parse_bracket_list(tokens: List[str]) -> List[str]:
    """Flatten bracketed lists: members [ v100 v200 ] → tokens without brackets."""
    out: List[str] = []
    buf: List[str] = []
    in_brackets = False
    for t in tokens:
        if t == "[":
            in_brackets = True
            buf = []
            continue
        if t == "]":
            in_brackets = False
            out.extend(buf)
            buf = []
            continue
        if in_brackets:
            buf.append(t)
        else:
            out.append(t)
    return out

def junos_vlan_catalog_from_set(set_text: str) -> Dict[str, int]:
    """Parse `show configuration vlans | display set` → { name_lower : VID }."""
    m: Dict[str, int] = {}
    for line in set_text.splitlines():
        line = line.strip()
        mm = CFG_SET_VLAN_VID_RE.match(line)
        if mm:
            name = mm.group(1).strip().lower()
            vid = int(mm.group(2))
            m[name] = vid
    return m

def junos_interfaces_from_set(set_text: str) -> Dict[str, dict]:
    """
    Parse `show configuration interfaces | display set` into per-unit map:
      { "ge-0/0/1.0": {"mode": "access"/"trunk", "access": name_or_vid, "native": name_or_vid, "tagged": [...] } }
    """
    per_unit: Dict[str, dict] = {}
    for raw in set_text.splitlines():
        line = raw.strip()
        if not line.startswith("set "):
            continue
        m = CFG_SET_IF_RE.match(line)
        if not m:
            continue
        ifname, unit, rest = m.group(1), m.group(2), m.group(3)
        key = f"{ifname}.{unit}"
        d = per_unit.setdefault(key, {"mode": None, "access": None, "native": None, "tagged": []})

        toks = _parse_bracket_list(rest.split())

        # interface-mode / port-mode
        if len(toks) >= 2 and toks[0] in ("interface-mode", "port-mode"):
            mode = toks[1].lower()
            if mode in ("access", "trunk"):
                d["mode"] = mode
            continue

        # vlan members <name|vid> [ list... ]
        if len(toks) >= 2 and toks[0] == "vlan" and toks[1] == "members":
            members = toks[2:]
            d["tagged"].extend(members)
            continue

        # native-vlan-id <vid>
        if len(toks) >= 2 and toks[0] == "native-vlan-id":
            try:
                d["native"] = int(toks[1])
            except Exception:
                d["native"] = toks[1]
            continue

        # native-vlan <name>
        if len(toks) >= 2 and toks[0] == "native-vlan":
            d["native"] = toks[1]
            continue

    # finalize: if mode not set, infer by number of members
    for k, v in per_unit.items():
        members = v.get("tagged") or []
        if not v.get("mode"):
            v["mode"] = "access" if len(members) <= 1 else "trunk"
        if v["mode"] == "access":
            v["access"] = members[0] if members else None
            v["tagged"] = []
            v["native"] = None
        else:
            # trunk: keep tagged; native may be set above
            pass

    return per_unit

def collapse_junos_units(per_unit_map: Dict[str, dict]) -> Dict[str, dict]:
    """Collapse logical units to physical and merge."""
    phys: Dict[str, dict] = {}
    for k, v in per_unit_map.items():
        base = k.split(".", 1)[0]
        cur = phys.get(base, {"mode": "access", "access": None, "native": None, "tagged": []})
        mode = v.get("mode") or "access"
        if mode == "trunk":
            cur["mode"] = "trunk"
            cur["tagged"] = sorted(set((cur.get("tagged") or []) + (v.get("tagged") or [])))
            if cur.get("native") is None and v.get("native") is not None:
                cur["native"] = v["native"]
            cur["access"] = None
        else:
            if cur.get("mode") != "trunk" and v.get("access") is not None:
                cur["access"] = v["access"]
        phys[base] = cur

    for k, v in phys.items():
        if v["mode"] == "access":
            v["tagged"] = []
            v["native"] = None
        else:
            v["access"] = None
            v["tagged"] = sorted(set(v["tagged"]))
    return phys

def resolve_token_to_vid(tok: Union[int, str, None], name_to_vid: Dict[str, int]) -> Optional[int]:
    if tok is None:
        return None
    if isinstance(tok, int):
        return tok
    return name_to_vid.get(str(tok).strip().lower())

# =================== MAIN ===================

def main():
    if not NETBOX_TOKEN or NETBOX_TOKEN.startswith("REPLACE_"):
        logging.error("Set NETBOX_TOKEN in env or edit script.")
        sys.exit(2)

    nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

    devices = [
        d for d in nb.dcim.devices.filter(role=DEVICE_ROLE_SLUG, status="active")
        if d.primary_ip4 or d.primary_ip6
    ]
    if args.device:
        devices = [d for d in devices if d.name == args.device]
        if not devices:
            logging.error(f"Device '{args.device}' not found or no primary IP.")
            sys.exit(1)

    total_changed = 0
    total_seen = 0

    for device in devices:
        name = device.name
        if not device.platform:
            logging.warning(f"{name} has no platform defined; skipping")
            continue

        try:
            device_type = map_platform_to_netmiko(device.platform.slug)
        except ValueError as e:
            logging.warning(f"{name}: {e}; skipping")
            continue

        ip_obj = device.primary_ip4 or device.primary_ip6
        ip_address = ip_obj.address.split("/")[0] if ip_obj else None
        if not ip_address:
            logging.warning(f"{name}: no primary IP; skipping")
            continue

        lookups = nb_vlan_lookups(nb, device.site)
        logging.info(f"[{name}] NetBox VLANs resolvable: {len(lookups.vid_to_id)}")

        conn_params = {
            "device_type": device_type,
            "host": ip_address,
            "username": SSH_USERNAME,
            "use_keys": True,
            "key_file": str(SSH_KEY_PATH),
        }

        try:
            net_connect = ConnectHandler(**conn_params)

            # -------- Collect per-platform --------
            if device_type == "juniper":
                # Config-first: build VLAN name→VID from display set
                vlans_set = net_connect.send_command("show configuration vlans | display set")
                name_to_vid = junos_vlan_catalog_from_set(vlans_set)

                if_set = net_connect.send_command("show configuration interfaces | display set")
                per_unit = junos_interfaces_from_set(if_set)
                sw_map = collapse_junos_units(per_unit)  # physical map {ge-*: {mode, access/name, native/name, tagged/names}}

                # Resolve any *names* in sw_map to VIDs using name_to_vid
                for ifn, desc in sw_map.items():
                    if desc.get("mode") == "access":
                        desc["access"] = resolve_token_to_vid(desc.get("access"), name_to_vid)
                        desc["native"] = None
                        desc["tagged"] = []
                    else:
                        desc["access"] = None
                        desc["native"] = resolve_token_to_vid(desc.get("native"), name_to_vid)
                        desc["tagged"] = sorted(set(
                            v for v in (resolve_token_to_vid(t, name_to_vid) for t in (desc.get("tagged") or []))
                            if v is not None
                        ))

            elif device_type == "arista_eos":
                text = net_connect.send_command("show interfaces switchport")
                sw_map = parse_eos_switchport(text)

            elif device_type in ("cisco_ios", "cisco_nxos"):
                text = net_connect.send_command("show interface switchport")
                sw_map = parse_ios_switchport(text)

            else:
                logging.warning(f"{name}: unsupported mapped device_type {device_type}; skipping")
                net_connect.disconnect()
                continue

            # -------- NetBox interfaces cache --------
            existing = {i.name: i for i in nb.dcim.interfaces.filter(device_id=device.id)}

            # -------- Device → NetBox authoritative sync --------
            for ifname, desc in sw_map.items():
                if should_ignore_interface(ifname):
                    continue
                total_seen += 1

                nb_name = ifname.split(".", 1)[0]  # physical match
                iface_obj = existing.get(nb_name) or existing.get(ifname)
                if not iface_obj:
                    logging.info(f"[{name}] skip {ifname} (not present in NetBox)")
                    continue

                mode_slug = "access" if desc.get("mode") == "access" else "tagged"

                access_vid = desc.get("access")
                native_vid = desc.get("native")
                tagged_vids = desc.get("tagged") or []

                untagged_id = None
                if mode_slug == "tagged" and native_vid is not None:
                    untagged_id = lookups.nb_id_from_vid(native_vid)
                elif mode_slug == "access" and access_vid is not None:
                    untagged_id = lookups.nb_id_from_vid(access_vid)

                tagged_ids = [lookups.nb_id_from_vid(v) for v in tagged_vids]
                tagged_ids = [v for v in tagged_ids if v is not None]

                desired = {
                    "mode": mode_slug,
                    "untagged_vlan": untagged_id,
                    "tagged_vlans": tagged_ids if mode_slug == "tagged" else [],
                }

                # Current NetBox state
                cur_mode = getattr(iface_obj, "mode", None)
                cur_untag = getattr(iface_obj, "untagged_vlan", None)
                cur_tagged = getattr(iface_obj, "tagged_vlans", None) or []
                cur_tagged_ids = [t["id"] if isinstance(t, dict) else t for t in cur_tagged]

                changed = (
                    cur_mode != desired["mode"] or
                    (cur_untag or None) != (desired["untagged_vlan"] or None) or
                    sorted(cur_tagged_ids) != sorted(desired["tagged_vlans"])
                )

                if changed:
                    if args.dry_run:
                        logging.info(f"[{name}] WOULD update {iface_obj.name} → {desired}")
                    else:
                        try:
                            iface_obj.update(desired)
                            logging.info(f"[{name}] updated {iface_obj.name} → {desired}")
                            total_changed += 1
                        except pynetbox.RequestError as e:
                            logging.error(f"[{name}] NetBox update error on {iface_obj.name}: {e.error}")
                else:
                    logging.debug(f"[{name}] nochange {iface_obj.name}")

            net_connect.disconnect()

        except (NetMikoTimeoutException, NetMikoAuthenticationException) as e:
            logging.error(f"[{name}] SSH failed: {e}")
            continue
        except Exception as e:
            logging.error(f"[{name}] unhandled error: {e}")
            continue

    logging.info(f"SUMMARY: seen=%s, netbox_changes=%s", total_seen, total_changed)

if __name__ == "__main__":
    main()
