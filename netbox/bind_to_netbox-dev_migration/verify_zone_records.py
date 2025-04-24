#!/usr/bin/env python3

import dns.resolver
import dns.zone
import dns.name
import dns.query
import dns.rdatatype
import dns.exception
import argparse
import os
import sys

def parse_zone_file(zone_file, origin):
    try:
        z = dns.zone.from_file(zone_file, origin)
        return z
    except dns.exception.DNSException as e:
        print(f"Error parsing zone file {zone_file}: {e}")
        sys.exit(1)

def fqdn_from_node(name, origin):
    return f"{name}.{origin}" if str(name) != "@" else str(origin)

def verify_record(fqdn, rdtype, resolver):
    try:
        resolver.resolve(fqdn, rdtype)
        return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Verify zone records against live DNS")
    parser.add_argument("zone_file", help="Path to BIND zone file")
    parser.add_argument("origin", help="Zone origin (e.g., example.com.)")
    parser.add_argument("--dns-server", help="DNS server IP to query (default: system resolver)")
    parser.add_argument("--skip-types", nargs="*", default=["SOA", "NS"], help="RR types to skip (default: SOA NS)")
    args = parser.parse_args()

    zone = parse_zone_file(args.zone_file, args.origin)
    resolver = dns.resolver.Resolver()

    if args.dns_server:
        resolver.nameservers = [args.dns_server]

    print(f"Checking records in zone: {args.origin}")
    failures = []

    for name, node in zone.nodes.items():
        fqdn = fqdn_from_node(name, args.origin)

        for rdataset in node.rdatasets:
            rdtype = dns.rdatatype.to_text(rdataset.rdtype)
            if rdtype in args.skip_types:
                continue

            if not verify_record(fqdn, rdtype, resolver):
                failures.append((fqdn, rdtype))

    if failures:
        print("\n❌ The following records failed to resolve:")
        for fqdn, rdtype in failures:
            print(f" - {fqdn} [{rdtype}]")
        sys.exit(2)
    else:
        print("\n✅ All records resolved successfully.")

if __name__ == "__main__":
    main()
