#!/usr/bin/env python3
# update_managed_records_ptr_locked_plus_plugin.py
#
# Behavior:
# 1) For each IP found in forward zones:
#    - If any forward FQDN exactly matches a reverse PTR target for that IP:
#         * Create/UPDATE NetBox IPAM ip_addresses.dns_name = that FQDN (PTR-locked)
#    - For any other forward FQDNs for this IP (mismatch to PTR):
#         * Create (or update) a NetBox-DNS plugin "record" with disable_ptr=True
# 2) For IPs with NO PTR at all:
#    - We CHECK NetBox-DNS first and only list candidates that actually need action
#      (needs_create or needs_update). Records that already exist with disable_ptr=True are filtered out.
#    - Prompt (or --auto-create-no-ptr) to create/update only those needing action.
#
# Flags:
#   --zone-folder            path to zone files (default: zones)
#   --dry-run                preview without writing
#   --auto-create-no-ptr     create/update plugin records for all no-PTR forwards needing action
#   --log-no-ptr-actions     log one line per no-PTR candidate during CHECK (exists_ok/needs_*)
#   --no-ptr-only            skip main pass and run only the no-PTR phase
#
# Requirements:
#   pip install pynetbox

import os
import sys
import time
import argparse
from collections import defaultdict, Counter
from datetime import datetime
from ipaddress import ip_network, ip_address, IPv6Address
import pynetbox

# =================== CONFIG ===================

NETBOX_URL   = '<URL>'
NETBOX_TOKEN = '<TOKEN>'
ZONE_FOLDER  = 'zones'

# ==============================================

nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

TYPE_SET  = {"A", "AAAA", "PTR"}
CLASS_SET = {"IN", "CH", "HS"}

def ts():
    return datetime.now().strftime("%H:%M:%S")

def out(msg):
    print(f"[{ts()}] {msg}", flush=True)

def strip_comments(line: str) -> str:
    return line.split(';', 1)[0].strip()

def is_ttl(tok: str) -> bool:
    return tok.isdigit()

def is_class(tok: str) -> bool:
    return tok.upper() in CLASS_SET

def is_type(tok: str) -> bool:
    return tok.upper() in TYPE_SET

def fqdn_from_owner(owner: str, origin: str) -> str:
    owner = owner.strip()
    if owner == "@":
        return origin.rstrip('.')
    if owner.endswith('.'):
        return owner[:-1]
    return f"{owner}.{origin.rstrip('.')}"

def is_reverse_zone(zone_name: str) -> bool:
    zl = zone_name.lower().rstrip('.')
    return zl.endswith('in-addr.arpa') or zl.endswith('ip6.arpa')

def reverse_owner_to_ip(owner_fqdn: str):
    name = owner_fqdn.rstrip('.').lower()

    if name.endswith('in-addr.arpa'):
        left = name.rsplit('.in-addr.arpa', 1)[0]
        labels = [l for l in left.split('.') if l]
        if len(labels) < 4:
            return None
        octets = labels[-4:]
        try:
            octets = [str(int(o)) for o in octets]
            if any(not (0 <= int(o) <= 255) for o in octets):
                return None
        except ValueError:
            return None
        return '.'.join(reversed(octets))

    if name.endswith('ip6.arpa'):
        left = name.rsplit('.ip6.arpa', 1)[0]
        nibbles = [l for l in left.split('.') if l]
        if len(nibbles) != 32:
            return None
        hexstr = ''.join(reversed(nibbles))
        try:
            val = int(hexstr, 16)
        except ValueError:
            return None
        return str(IPv6Address(val))

    return None

def normalize_ptr_target(target: str) -> str:
    t = target.strip()
    return t[:-1] if t.endswith('.') else t

def load_zone_filenames(folder):
    try:
        return sorted([f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))])
    except FileNotFoundError:
        raise SystemExit(f"[ERROR] Folder not found: {folder}")

# ---------- small retry & helper wrappers ----------

def with_retries(fn, *args, attempts=3, wait_s=0.6, **kwargs):
    last_exc = None
    for i in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if i == attempts:
                raise
            time.sleep(wait_s)
    raise last_exc

def endpoint_update_one(endpoint, data_dict):
    """
    Bulk-style endpoint update for a single object.
    pynetbox Endpoint.update expects a LIST of dicts, each with 'id'.
    """
    return with_retries(endpoint.update, [data_dict])

# --------- Generic RR line parser ---------

def parse_rr_tokens(tokens, current_owner, origin):
    """
    [owner] [TTL] [CLASS] TYPE RDATA
    or with owner omitted (inherit), TTL-first, etc.
    """
    i = 0
    owner = None

    if i < len(tokens):
        t0 = tokens[i]
        u0 = t0.upper()
        if not is_ttl(t0) and not is_class(u0) and not is_type(u0):
            owner = t0
            current_owner = owner
            i += 1

    if i < len(tokens) and is_ttl(tokens[i]):
        i += 1

    if i < len(tokens) and is_class(tokens[i].upper()):
        i += 1

    if i >= len(tokens) or not is_type(tokens[i].upper()):
        return None
    rtype = tokens[i].upper()
    i += 1

    if i >= len(tokens):
        return None
    rdata = tokens[i]

    owner_text = owner if owner is not None else current_owner
    if not owner_text:
        owner_text = "@"

    owner_fqdn = fqdn_from_owner(owner_text, origin)
    return owner_fqdn, rtype, rdata, current_owner

# --------- Parsing passes ---------

def parse_forward_records(folder):
    out(f"Scanning forward zones in '{folder}'...")
    recs = []
    for filename in load_zone_filenames(folder):
        if is_reverse_zone(filename):
            continue
        origin = filename.rstrip('.')
        current_owner = "@"

        path = os.path.join(folder, filename)
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            for raw in fh:
                line = strip_comments(raw)
                if not line:
                    continue
                if line.startswith('$'):
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].upper() == '$ORIGIN':
                        origin = parts[1].rstrip('.')
                    continue

                tokens = line.split()
                parsed = parse_rr_tokens(tokens, current_owner, origin)
                if not parsed:
                    continue
                owner_fqdn, rtype, rdata, current_owner = parsed

                if rtype in ("A", "AAAA"):
                    recs.append((owner_fqdn, rtype, rdata))  # (fqdn, type, ip)
    out(f"Forward A/AAAA discovered: {len(recs)}")
    return recs

def parse_reverse_ptrs(folder):
    out(f"Scanning reverse zones in '{folder}'...")
    ip_to_targets = defaultdict(set)

    for filename in load_zone_filenames(folder):
        if not is_reverse_zone(filename):
            continue
        origin = filename.rstrip('.')
        current_owner = "@"

        path = os.path.join(folder, filename)
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            for raw in fh:
                line = strip_comments(raw)
                if not line:
                    continue
                if line.startswith('$'):
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].upper() == '$ORIGIN':
                        origin = parts[1].rstrip('.')
                    continue

                tokens = line.split()
                parsed = parse_rr_tokens(tokens, current_owner, origin)
                if not parsed:
                    continue
                owner_fqdn, rtype, rdata, current_owner = parsed

                if rtype == "PTR":
                    ip_str = reverse_owner_to_ip(owner_fqdn)
                    if not ip_str:
                        continue
                    target = normalize_ptr_target(rdata)
                    ip_to_targets[ip_str].add(target)

    out(f"Reverse PTR unique IPs: {len(ip_to_targets)}")
    return ip_to_targets

# --------- Helpers for NetBox ---------

def build_ip_candidates(forward_records):
    by_ip = defaultdict(list)  # ip -> list of (fqdn, rtype)
    for fqdn, rtype, ip in forward_records:
        by_ip[ip].append((fqdn, rtype))
    return by_ip

class PrefixIndex:
    def __init__(self):
        self.v4 = []
        self.v6 = []
        self._load()
    def _load(self):
        out("[NetBox] Loading prefixes...")
        for p in nb.ipam.prefixes.all():
            try:
                net = ip_network(p.prefix, strict=False)
            except Exception:
                continue
            (self.v4 if net.version == 4 else self.v6).append((p.prefix, net))
        self.v4.sort(key=lambda x: x[1].prefixlen, reverse=True)
        self.v6.sort(key=lambda x: x[1].prefixlen, reverse=True)
        out(f"[NetBox] Prefixes loaded: v4={len(self.v4)} v6={len(self.v6)}")
    def best_prefix(self, ip_str):
        ip_obj = ip_address(ip_str)
        nets = self.v4 if ip_obj.version == 4 else self.v6
        for raw, net in nets:
            if ip_obj in net:
                return raw
        return None

def create_or_update_ip(ip, fqdn, prefix_index, dry_run=False):
    """
    If IP exists: overwrite dns_name to fqdn (PTR-verified).
    If missing: create under best matching prefix with dns_name=fqdn.
    Uses Endpoint.update([{"id":..., ...}]) signature.
    """
    try:
        existing = with_retries(nb.ipam.ip_addresses.get, address=ip)
    except Exception as e:
        out(f"[WARN] NetBox IP lookup failed for {ip} (intended dns_name='{fqdn}'): {e}")
        return (False, False)

    if existing:
        current = (existing.dns_name or "").strip()
        if current == fqdn:
            return (False, False)
        if dry_run:
            out(f"DRY-RUN: UPDATE IP {ip} dns_name: '{current}' -> '{fqdn}'")
            return (False, True)
        try:
            endpoint_update_one(nb.ipam.ip_addresses, {"id": existing.id, "dns_name": fqdn})
            out(f"UPDATED IP {ip} dns_name: '{current}' -> '{fqdn}'")
            return (False, True)
        except Exception as e:
            out(f"[ERROR] Failed to update IP {ip} (set dns_name='{fqdn}'): {e}")
            return (False, False)

    best = prefix_index.best_prefix(ip)
    if not best:
        out(f"[WARN] No matching prefix for {ip}; skipping create for dns_name='{fqdn}'.")
        return (False, False)

    mask = best.split('/', 1)[1]
    address_cidr = f"{ip}/{mask}"

    if dry_run:
        out(f"DRY-RUN: CREATE IP {address_cidr} dns_name='{fqdn}'")
        return (True, False)

    try:
        with_retries(nb.ipam.ip_addresses.create, {"address": address_cidr, "dns_name": fqdn})
        out(f"CREATED IP {address_cidr} dns_name='{fqdn}'")
        return (True, False)
    except Exception as e:
        out(f"[ERROR] Failed to create IP {address_cidr} (dns_name='{fqdn}'): {e}")
        return (False, False)

# ---- NetBox-DNS plugin helpers ----

def load_dns_zones():
    out("[NetBox-DNS] Loading zones...")
    zones = []
    for z in nb.plugins.netbox_dns.zones.all():
        zones.append((z.id, z.name.rstrip('.')))
    zones.sort(key=lambda x: len(x[1]), reverse=True)
    out(f"[NetBox-DNS] Zones loaded: {len(zones)}")
    return zones

def choose_zone_for_fqdn(fqdn, zones):
    fq = fqdn.rstrip('.')
    for zid, zname in zones:
        if fq == zname:
            return zid, zname, "@"
        if fq.endswith("." + zname):
            rel = fq[:-(len(zname)+1)]
            return zid, zname, rel
    return None, None, None

def plugin_check_status(zones, fqdn, rtype, ip_value):
    """
    Check-only: return one of:
      - exists_ok (record exists and disable_ptr=True)
      - needs_update (record exists but disable_ptr=False)
      - needs_create (no such record)
      - no_zone / search_error
    """
    zid, _zname, rel = choose_zone_for_fqdn(fqdn, zones)
    if not zid:
        return "no_zone"

    try:
        existing = list(nb.plugins.netbox_dns.records.filter(
            zone_id=zid, type=rtype, name=rel, value=ip_value
        ))
    except Exception:
        return "search_error"

    if not existing:
        return "needs_create"

    for rec in existing:
        if not getattr(rec, "disable_ptr", False):
            return "needs_update"
    return "exists_ok"

def ensure_plugin_record(zones, fqdn, rtype, ip_value, dry_run=False):
    """
    Create/update record with disable_ptr=True.
    Returns (created:bool, updated:bool, reason:str)
    Reasons: created, updated, exists_ok, no_zone, search_error, update_error, create_error
    """
    status = plugin_check_status(zones, fqdn, rtype, ip_value)
    if status == "no_zone":
        out(f"[WARN] No matching DNS zone found for FQDN '{fqdn}'; skipping plugin record.")
        return (False, False, "no_zone")
    if status == "search_error":
        out(f"[WARN] NetBox-DNS search failed for {fqdn} [{rtype}] -> {ip_value}")
        return (False, False, "search_error")
    if status == "exists_ok":
        return (False, False, "exists_ok")
    if status == "needs_update":
        if dry_run:
            out(f"DRY-RUN: would set disable_ptr=True for {fqdn} [{rtype}] -> {ip_value}")
            return (False, True, "updated")
        zid, _zname, rel = choose_zone_for_fqdn(fqdn, zones)
        try:
            recs = list(nb.plugins.netbox_dns.records.filter(
                zone_id=zid, type=rtype, name=rel, value=ip_value
            ))
        except Exception:
            return (False, False, "search_error")
        any_updated = False
        for rec in recs:
            if getattr(rec, "disable_ptr", False):
                continue
            try:
                endpoint_update_one(nb.plugins.netbox_dns.records, {"id": rec.id, "disable_ptr": True})
                any_updated = True
            except Exception as e:
                out(f"[ERROR] Failed to update DNS record id={rec.id} ({fqdn} [{rtype}] -> {ip_value}): {e}")
                return (False, False, "update_error")
        return (False, True, "updated") if any_updated else (False, False, "exists_ok")

    # needs_create
    if dry_run:
        out(f"DRY-RUN: CREATE DNS record {fqdn} [{rtype}] -> {ip_value} (disable_ptr=True)")
        return (True, False, "created")

    zid, _zname, rel = choose_zone_for_fqdn(fqdn, zones)
    payload = {
        "zone": zid,
        "type": rtype,
        "name": rel,
        "value": ip_value,
        "disable_ptr": True,
        "managed": False,
    }
    try:
        with_retries(nb.plugins.netbox_dns.records.create, payload)
        out(f"CREATED DNS record {fqdn} [{rtype}] -> {ip_value} (disable_ptr=True)")
        return (True, False, "created")
    except Exception as e:
        out(f"[ERROR] Failed to create DNS record for {fqdn} [{rtype}] -> {ip_value}: {e}")
        return (False, False, "create_error")

# --------- Main ---------

def main():
    ap = argparse.ArgumentParser(description="PTR-locked IP updates + plugin records; no-PTR list only includes items needing action.")
    ap.add_argument("--zone-folder", default=ZONE_FOLDER, help="Path to zone files folder (default: zones)")
    ap.add_argument("--dry-run", action="store_true", help="Show actions without writing to NetBox.")
    ap.add_argument("--auto-create-no-ptr", action="store_true", help="Automatically create/update plugin records for all no-PTR forwards needing action.")
    ap.add_argument("--log-no-ptr-actions", action="store_true", help="Log one line per no-PTR candidate during CHECK (exists_ok/needs_*).")
    ap.add_argument("--no-ptr-only", action="store_true", help="Skip main pass and run only the no-PTR phase.")
    args = ap.parse_args()

    out("=== update_managed_records_ptr_locked_plus_plugin.py starting ===")
    out(f"Zone folder : {args.zone_folder}")
    out(f"Dry run     : {args.dry_run}")
    out(f"Auto no-PTR : {args.auto_create_no_ptr}")
    out(f"Log no-PTR  : {args.log_no_ptr_actions}")
    out(f"NetBox URL  : {NETBOX_URL}")

    # Parse BIND
    forward = parse_forward_records(args.zone_folder)       # [(fqdn, rtype, ip)]
    reverse_map = parse_reverse_ptrs(args.zone_folder)      # ip -> {ptr_targets}
    by_ip = build_ip_candidates(forward)                    # ip -> [(fqdn, rtype)]
    out(f"Unique IPs observed in A/AAAA: {len(by_ip)}")

    dns_zones = load_dns_zones()

    created_ip = 0
    updated_ip = 0
    created_dns = 0
    updated_dns = 0

    # Build the no-PTR worklist with only items that need action
    worklist = []
    total_checked = 0
    filtered_exists_ok = 0
    filtered_no_zone = 0
    filtered_search_err = 0

    if not args.no_ptr_only:
        prefix_index = PrefixIndex()
        out("Evaluating IPs…")
        for ip, pairs in by_ip.items():
            ptr_targets = reverse_map.get(ip, set())

            if not ptr_targets:
                needing = []
                for fqdn, rtype in pairs:
                    status = plugin_check_status(dns_zones, fqdn, rtype, ip)
                    total_checked += 1
                    if args.log_no_ptr_actions:
                        out(f"[NO-PTR CHECK] {fqdn} [{rtype}] -> {ip}: {status}")
                    if status == "exists_ok":
                        filtered_exists_ok += 1
                        continue
                    if status == "no_zone":
                        filtered_no_zone += 1
                        continue
                    if status == "search_error":
                        filtered_search_err += 1
                        continue
                    # needs_create or needs_update
                    needing.append((fqdn, rtype, status))
                if needing:
                    worklist.append({"ip": ip, "candidates": needing})
                continue

            # PTR-locked IPAM dns_name
            chosen = next((fq for (fq, _rt) in pairs if fq in ptr_targets), None)
            if chosen:
                c,u = create_or_update_ip(ip, chosen, prefix_index, dry_run=args.dry_run)
                created_ip += 1 if c else 0
                updated_ip += 1 if u else 0

            # Mismatched forward names -> plugin records (disable_ptr=True)
            for fqdn, rtype in pairs:
                if fqdn == chosen:
                    continue
                c,u,reason = ensure_plugin_record(dns_zones, fqdn, rtype, ip, dry_run=args.dry_run)
                created_dns += 1 if c else 0
                updated_dns += 1 if u else 0
    else:
        out("Building no-PTR worklist (no-ptr-only)…")
        for ip, pairs in by_ip.items():
            if ip in reverse_map and reverse_map[ip]:
                continue
            needing = []
            for fqdn, rtype in pairs:
                status = plugin_check_status(dns_zones, fqdn, rtype, ip)
                total_checked += 1
                if args.log_no_ptr_actions:
                    out(f"[NO-PTR CHECK] {fqdn} [{rtype}] -> {ip}: {status}")
                if status == "exists_ok":
                    filtered_exists_ok += 1
                    continue
                if status == "no_zone":
                    filtered_no_zone += 1
                    continue
                if status == "search_error":
                    filtered_search_err += 1
                    continue
                needing.append((fqdn, rtype, status))
            if needing:
                worklist.append({"ip": ip, "candidates": needing})

    out("=== SUMMARY ===")
    out(f"IPAM created         : {created_ip}{' (dry-run)' if args.dry_run else ''}")
    out(f"IPAM updated dns_name: {updated_ip}{' (dry-run)' if args.dry_run else ''}")
    out(f"Plugin created       : {created_dns}{' (dry-run)' if args.dry_run else ''}")
    out(f"Plugin updated (ptr) : {updated_dns}{' (dry-run)' if args.dry_run else ''}")

    # Report what we filtered from the no-PTR list
    out(f"No-PTR candidates checked : {total_checked}")
    out(f"Filtered (exists_ok)      : {filtered_exists_ok}")
    out(f"Filtered (no_zone)        : {filtered_no_zone}")
    out(f"Filtered (search_error)   : {filtered_search_err}")
    out(f"No-PTR needing action     : {sum(len(x['candidates']) for x in worklist)}")

    if worklist:
        print("\n[NO-PTR - NEED ACTION]", flush=True)
        for item in worklist:
            ip = item["ip"]
            cands = ", ".join(f'{fq} [{rt}] ({reason})' for (fq, rt, reason) in item["candidates"])
            print(f" - {ip}: candidates=[{cands}]", flush=True)

        proceed = False
        if args.auto_create_no_ptr:
            proceed = True
        else:
            if sys.stdin.isatty():
                try:
                    ans = input("\nCreate/Update plugin records (disable_ptr=True) for ALL no-PTR forwards needing action? [y/N]: ").strip().lower()
                    proceed = ans == "y"
                except EOFError:
                    proceed = False
            else:
                out("[INFO] Non-interactive session; not creating no-PTR plugin records (use --auto-create-no-ptr).")

        if proceed:
            out("Creating/updating plugin records for no-PTR forwards needing action…")
            reason_counts = Counter()
            created_np = 0
            updated_np = 0
            for item in worklist:
                ip = item["ip"]
                for fqdn, rtype, _status in item["candidates"]:
                    c,u,reason = ensure_plugin_record(dns_zones, fqdn, rtype, ip, dry_run=args.dry_run)
                    created_np += 1 if c else 0
                    updated_np += 1 if u else 0
                    reason_counts[reason] += 1
            out(f"[NO-PTR] Plugin created: {created_np}{' (dry-run)' if args.dry_run else ''}")
            out(f"[NO-PTR] Plugin updated: {updated_np}{' (dry-run)' if args.dry_run else ''}")
            if reason_counts:
                summary = ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items()))
                out(f"[NO-PTR] Outcome summary: {summary}")
        else:
            out("Skipped creation/update of no-PTR plugin records.")
    else:
        out("No no-PTR forwards require action.")

    out("Done.")

if __name__ == "__main__":
    main()
