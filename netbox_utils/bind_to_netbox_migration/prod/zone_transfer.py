#!/usr/bin/env python3

import os
import sys
import dns.query
import dns.zone
import dns.exception
import socket
import traceback


# Define the input file, output directory, and DNS server
ZONE_LIST_FILE="zones.txt"
OUTPUT_DIR="zones"
DEFAULT_DNS_SERVER="8.8.8.8" # Default DNS server (change as needed)

# Allow DNS server override via CLI
dns_server = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DNS_SERVER
print(f"Using DNS server: {dns_server}")

# Resolve DNS server to IP
try:
    dns_server_ip = socket.gethostbyname(dns_server)
except socket.gaierror as e:
    print(f"Could not resolve DNS server '{dns_server}': {e}")
    sys.exit(1)

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Check zone list file exists
if not os.path.isfile(ZONE_LIST_FILE):
    print(f"Error: {ZONE_LIST_FILE} does not exist.")
    sys.exit(1)

# Read zones and perform AXFR
with open(ZONE_LIST_FILE, "r") as file:
    for line in file:
        zone_name = line.strip()
        if not zone_name or zone_name.startswith("#"):
            continue

        print(f"Performing zone transfer for: {zone_name}")
        output_file = os.path.join(OUTPUT_DIR, zone_name)

        try:
            xfr = dns.query.xfr(dns_server_ip, zone_name, lifetime=30)
            zone = dns.zone.from_xfr(list(xfr))  # Fully consume generator

            if zone is None or not zone.nodes:
                print(f"Zone transfer for {zone_name} returned no records.")
                continue

            with open(output_file, "w") as f:
                for name, node in zone.nodes.items():
                    for rdataset in node.rdatasets:
                        f.write(f"{name.to_text()}\t{rdataset.to_text()}\n")

            print(f"Zone transfer for {zone_name} completed and saved to {output_file}")

        except dns.exception.FormError:
            print(f"FormError: Server refused zone transfer for {zone_name}")
        except dns.exception.Timeout:
            print(f"Timeout: No response from server for {zone_name}")
        except dns.query.BadResponse as e:
            print(f"Bad response from server for {zone_name}: {e}")
        except Exception as e:
            print(f"Unexpected error for {zone_name}: {e}")
            traceback.print_exc()
