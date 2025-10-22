#!/usr/bin/env python3
import argparse
import socket
import sys

import dns.zone
import dns.name
import dns.exception
import dns.rdatatype
import dns.reversename
import dns.message
import dns.query
import dns.rcode
import dns.resolver


def normalize_fqdn(s: str) -> str:
    s = (s or "").strip().lower()
    return s if s.endswith(".") else s + "."


def resolve_dns_server(server: str) -> str:
    try:
        for info in socket.getaddrinfo(server, 53):
            return info[4][0]
        raise RuntimeError("No usable IP from getaddrinfo()")
    except Exception as e:
        print(f"❌ Failed to resolve DNS server '{server}': {e}")
        sys.exit(1)


def query_ptr_targets(ip: str, dns_server_ip: str | None, timeout: float) -> set[str]:
    """Return set of PTR target FQDNs (with trailing dot). Empty set if none."""
    try:
        revname = dns.reversename.from_address(ip)
    except Exception:
        return set()

    try:
        if dns_server_ip:
            q = dns.message.make_query(revname, dns.rdatatype.PTR)
            r = dns.query.udp(q, dns_server_ip, timeout=timeout)
            if r.rcode() != dns.rcode.NOERROR or not r.answer:
                return set()
            out = set()
            for ans in r.answer:
                if ans.rdtype == dns.rdatatype.PTR:
                    for rdata in ans:
                        out.add(normalize_fqdn(rdata.target.to_text()))
            return out
        else:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = timeout
            resolver.timeout = timeout
            answers = resolver.resolve(revname, "PTR")
            return {normalize_fqdn(rr.target.to_text()) for rr in answers}
    except Exception:
        return set()


def main():
    p = argparse.ArgumentParser(
        description="Print A/AAAA records whose PTR does not match the forward FQDN."
    )
    p.add_argument("zone_file", help="Path to BIND zone file")
    p.add_argument("origin", help="Zone origin, e.g. example.com. (trailing dot optional)")
    p.add_argument("--dns-server", help="DNS server (IP/host) for PTR lookups")
    p.add_argument("--timeout", type=float, default=2.0, help="DNS query timeout (s)")
    mx = p.add_mutually_exclusive_group()
    mx.add_argument("--only-a", action="store_true", help="Check only A records")
    mx.add_argument("--only-aaaa", action="store_true", help="Check only AAAA records")
    p.add_argument("--include-missing", action="store_true",
                   help="Also print when no PTR exists (default: mismatches only)")
    args = p.parse_args()

    # Normalize origin to no trailing dot for dnspython
    origin_no_dot = args.origin.rstrip(".")
    origin_name = dns.name.from_text(origin_no_dot)

    # Parse zone (let dnspython handle TTLs/owners correctly)
    try:
        z = dns.zone.from_file(args.zone_file, origin_no_dot)
    except dns.exception.DNSException as e:
        print(f"Error parsing zone file {args.zone_file}: {e}")
        sys.exit(1)

    # Which rrtypes to check
    if args.only_a:
        types_to_check = [dns.rdatatype.A]
    elif args.only_aaaa:
        types_to_check = [dns.rdatatype.AAAA]
    else:
        types_to_check = [dns.rdatatype.A, dns.rdatatype.AAAA]

    dns_server_ip = resolve_dns_server(args.dns_server) if args.dns_server else None

    mismatches = []   # (fqdn, 'A'/'AAAA', ip, targets_str)
    missing = []      # (fqdn, 'A'/'AAAA', ip)
    total_checked = 0

    for rdtype in types_to_check:
        for (owner_name, _ttl, rdata) in z.iterate_rdatas(rdtype):
            # Convert relative owner to absolute under the zone origin
            abs_name = owner_name.derelativize(origin_name)
            fqdn_abs = normalize_fqdn(abs_name.to_text())
            ip = getattr(rdata, "address", None)
            if not ip:
                continue
            total_checked += 1

            ptr_targets = query_ptr_targets(ip, dns_server_ip, args.timeout)
            if not ptr_targets:
                if args.include_missing:
                    tname = "A" if rdtype == dns.rdatatype.A else "AAAA"
                    missing.append((fqdn_abs, tname, ip))
                continue

            if fqdn_abs not in ptr_targets:
                tname = "A" if rdtype == dns.rdatatype.A else "AAAA"
                mismatches.append((fqdn_abs, tname, ip, ", ".join(sorted(ptr_targets))))

    if mismatches:
        print("❌ PTR mismatches (PTR exists but does not match forward name):")
        for fqdn, rrt, ip, targets in mismatches:
            print(f" - {fqdn} [{rrt}] {ip} -> PTR(s): {targets}")
    else:
        print("✅ No PTR mismatches found.")

    if args.include_missing:
        if missing:
            print("\n⚠️ No PTR found for these records:")
            for fqdn, rrt, ip in missing:
                print(f" - {fqdn} [{rrt}] {ip} -> (no PTR)")
        else:
            print("\nℹ️ No records missing PTRs.")

    # Optional:
    # print(f"\nChecked {total_checked} A/AAAA records.")

if __name__ == "__main__":
    main()

