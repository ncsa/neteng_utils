#!/usr/bin/python3
import re
import sys
import ipaddress
import pynetbox
from typing import Optional, Tuple

# NetBox Configuration
NETBOX_URL = '<URL>'
NETBOX_TOKEN = '<TOKEN>'
CUSTOM_FIELD_NAME = "mac_address"

# File containing ISC dhcpd.conf
DHCPD_CONF_FILE = "dhcpd.conf"

# Initialize pynetbox API instance
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

# -------- Parsing dhcpd.conf --------
# Match: host <name> { ... hardware ethernet XX:XX:...; ... fixed-address 1.2.3.4; ... }
HOST_BLOCK_REGEX = re.compile(
    r"""
    host\s+[\w\-\.\_]+\s*      # 'host <name>'
    \{                         # opening brace
    (?P<body>.*?)              # capture body non-greedily
    \}                         # closing brace
    """,
    re.DOTALL | re.VERBOSE
)

FIXED_ADDRESS_REGEX = re.compile(
    r"""fixed-address\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})(?:\s*,\s*[0-9]{1,3}(?:\.[0-9]{1,3}){3})*\s*;""",
    re.IGNORECASE
)

HARDWARE_ETH_REGEX = re.compile(
    r"""hardware\s+ethernet\s+([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s*;""",
    re.IGNORECASE
)

def normalize_mac(mac: str) -> str:
    return mac.strip().upper()

def clear_all_mac_addresses():
    """Clears the custom field 'mac_address' from all IP addresses in NetBox."""
    print("Step 1/3: Clearing existing MAC custom-field values in NetBox…")
    total = 0
    cleared = 0
    for ip_obj in nb.ipam.ip_addresses.all():
        total += 1
        cf = ip_obj.custom_fields or {}
        if cf.get(CUSTOM_FIELD_NAME):
            cf[CUSTOM_FIELD_NAME] = None
            ip_obj.custom_fields = cf
            try:
                ip_obj.save()
                cleared += 1
                print(f"  - Cleared MAC for {ip_obj.address}")
            except Exception as e:
                print(f"  ! Failed to clear MAC for {ip_obj.address}: {e}")
    print(f"Cleared {cleared} of {total} IPs.\n")

def parse_dhcpd_conf(file_path: str) -> dict[str, str]:
    """
    Extract { ip -> mac } from dhcpd.conf by scanning host blocks that contain
    both a fixed-address and a hardware ethernet.
    """
    print("Step 2/3: Parsing dhcpd.conf for reservations…")
    with open(file_path, "r") as f:
        content = f.read()

    reservations: dict[str, str] = {}
    blocks = list(HOST_BLOCK_REGEX.finditer(content))
    print(f"  Found {len(blocks)} 'host {{ … }}' blocks.")

    for m in blocks:
        body = m.group("body")
        fa = FIXED_ADDRESS_REGEX.search(body)
        hw = HARDWARE_ETH_REGEX.search(body)
        if not fa or not hw:
            continue
        ip = fa.group(1)
        mac = normalize_mac(hw.group(1))
        reservations[ip] = mac  # last one wins if dup IPs
        print(f"  - Reservation: {ip} -> {mac}")

    print(f"Parsed {len(reservations)} IP→MAC reservations.\n")
    return reservations

# -------- NetBox helpers --------
def most_specific_parent_prefix(ip_str: str):
    """
    Return the most-specific NetBox Prefix object that contains ip_str.
    If multiple, choose the one with the largest prefix length.
    """
    try:
        # NetBox supports filtering prefixes by 'contains=<ip>'
        candidates = list(nb.ipam.prefixes.filter(contains=ip_str))
    except Exception as e:
        print(f"  ! Error fetching parent prefix for {ip_str}: {e}")
        return None

    if not candidates:
        return None

    # Choose the longest prefix length (most specific)
    def plen(p):
        # p.prefix like "192.0.2.0/24"
        return int(p.prefix.split("/")[1])

    candidates.sort(key=plen, reverse=True)
    return candidates[0]

def format_address_with_parent_length(ip_str: str, parent_prefix) -> Optional[str]:
    """
    Build 'ip/prefixlen' using the parent prefix length. Returns None if invalid.
    """
    try:
        ipaddress.ip_address(ip_str)  # validate
    except ValueError:
        print(f"  ! Invalid IP '{ip_str}', skipping.")
        return None

    try:
        plen = int(parent_prefix.prefix.split("/")[1])
    except Exception:
        print(f"  ! Could not parse parent prefix length for {ip_str} from {parent_prefix.prefix}")
        return None

    return f"{ip_str}/{plen}"

def get_ip_obj(ip_cidr: str, vrf_id: Optional[int] = None):
    """
    Try to retrieve an IP by exact 'address'. If VRF is provided, filter by it
    to avoid ambiguity across VRFs.
    """
    if vrf_id:
        objs = list(nb.ipam.ip_addresses.filter(address=ip_cidr, vrf_id=vrf_id))
        if objs:
            return objs[0]
    # Fallback: global search by address
    return nb.ipam.ip_addresses.get(address=ip_cidr)

def create_ip_in_prefix(ip_cidr: str, parent_prefix) -> Optional[object]:
    """
    Create an IP address in NetBox using the parent prefix's VRF and tenant when available.
    """
    payload = {"address": ip_cidr, "status": "active"}

    # Attach VRF and tenant if present on the parent prefix
    try:
        if getattr(parent_prefix, "vrf", None):
            payload["vrf"] = parent_prefix.vrf.id
    except Exception:
        pass

    try:
        if getattr(parent_prefix, "tenant", None):
            payload["tenant"] = parent_prefix.tenant.id
    except Exception:
        pass

    try:
        ip_obj = nb.ipam.ip_addresses.create(payload)
        print(f"  + Created IP {ip_cidr} "
              f"(vrf={getattr(parent_prefix.vrf, 'name', '—') if getattr(parent_prefix, 'vrf', None) else 'global'}, "
              f"tenant={getattr(parent_prefix.tenant, 'name', '—') if getattr(parent_prefix, 'tenant', None) else '—'})")
        return ip_obj
    except Exception as e:
        print(f"  ! Failed to create IP {ip_cidr}: {e}")
        return None

def get_or_create_ip_using_parent(ip_str: str) -> Tuple[Optional[object], bool]:
    """
    Find the most-specific parent prefix for ip_str; return (ip_obj, was_created).
    If no parent prefix exists in NetBox, we skip creation (return (None, False)).
    """
    parent = most_specific_parent_prefix(ip_str)
    if not parent:
        print(f"  ! No parent Prefix found in NetBox for {ip_str}; skipping creation.")
        return None, False

    ip_cidr = format_address_with_parent_length(ip_str, parent)
    if not ip_cidr:
        return None, False

    vrf_id = getattr(parent.vrf, "id", None) if getattr(parent, "vrf", None) else None
    ip_obj = get_ip_obj(ip_cidr, vrf_id=vrf_id)
    if ip_obj:
        return ip_obj, False

    # Create new IP inside the parent's VRF/tenant
    ip_obj = create_ip_in_prefix(ip_cidr, parent)
    return ip_obj, True if ip_obj else False

def set_mac_on_ip(ip_obj, mac: str) -> bool:
    """Set custom field MAC on a NetBox IP object."""
    cf = ip_obj.custom_fields or {}
    cf[CUSTOM_FIELD_NAME] = mac
    ip_obj.custom_fields = cf
    try:
        ip_obj.save()
        print(f"  ✓ Set MAC {mac} on {ip_obj.address}")
        return True
    except Exception as e:
        print(f"  ! Failed to set MAC {mac} on {ip_obj.address}: {e}")
        return False

def apply_reservations(reservations: dict[str, str]):
    """Ensure each reservation IP exists under its parent Prefix length and has the MAC custom field set."""
    print("Step 3/3: Applying reservations to NetBox…")
    created = updated = skipped = failures = 0

    for ip, mac in reservations.items():
        # get or create using the parent prefix length
        ip_obj, was_created = get_or_create_ip_using_parent(ip)
        if not ip_obj:
            skipped += 1
            continue
        if was_created:
            created += 1
        if set_mac_on_ip(ip_obj, mac):
            updated += 1
        else:
            failures += 1

    print(f"\nDone. Created IPs: {created}, Updated MACs: {updated}, "
          f"Skipped (no parent prefix): {skipped}, Failures: {failures}")

def main():
    try:
        clear_all_mac_addresses()
        reservations = parse_dhcpd_conf(DHCPD_CONF_FILE)
        apply_reservations(reservations)
    except FileNotFoundError:
        print(f"ERROR: Could not open {DHCPD_CONF_FILE}.")
        sys.exit(1)

if __name__ == "__main__":
    main()
