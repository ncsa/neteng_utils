#!/usr/bin/env python3

import dns.zone
import dns.name
import dns.query
import dns.rdatatype
import dns.message
import dns.rcode
import dns.exception
import socket
import argparse
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

def resolve_dns_server(server):
    try:
        # getaddrinfo returns tuples; extract the IP
        addr_info = socket.getaddrinfo(server, 53)
        for info in addr_info:
            ip = info[4][0]
            return ip
        raise Exception("No usable IP address found")
    except Exception as e:
        print(f"❌ Failed to resolve DNS server '{server}': {e}")
        sys.exit(1)

def verify_record(fqdn, rdtype, dns_server=None):
    try:
        query = dns.message.make_query(fqdn, rdtype)
        response = dns.query.udp(query, dns_server, timeout=2)
        return response.rcode() == dns.rcode.NOERROR and len(response.answer) > 0
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Verify zone records against live DNS")
    parser.add_argument("zone_file", help="Path to BIND zone file")
    parser.add_argument("origin", help="Zone origin (e.g., example.com.)")
    parser.add_argument("--dns-server", help="DNS server IP or hostname to query (IPv4 or IPv6)")
    parser.add_argument("--skip-types", nargs="*", default=["SOA", "NS"], help="RR types to skip (default: SOA NS)")
    args = parser.parse_args()

    dns_server = None
    if args.dns_server:
        dns_server = resolve_dns_server(args.dns_server)

    zone = parse_zone_file(args.zone_file, args.origin)

    print(f"🔍 Checking records in zone: {args.origin}")
    failures = []

    for name, node in zone.nodes.items():
        fqdn = fqdn_from_node(name, args.origin)

        for rdataset in node.rdatasets:
            rdtype = dns.rdatatype.to_text(rdataset.rdtype)
            if rdtype in args.skip_types:
                continue

            if not verify_record(fqdn, rdtype, dns_server=dns_server):
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

