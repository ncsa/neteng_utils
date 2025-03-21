#!/usr/bin/env python3

import os
import subprocess
import sys

# Config
ZONE_LIST_FILE = "zones.txt"
OUTPUT_DIR = "zones"
DEFAULT_DNS_SERVER = "<dns.server.com>"

# Allow DNS server override via CLI argument
dns_server = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DNS_SERVER
print(f"Using DNS server: {dns_server}")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Check if zone list file exists
if not os.path.isfile(ZONE_LIST_FILE):
    print(f"Error: {ZONE_LIST_FILE} does not exist.")
    sys.exit(1)

# Read zones and perform AXFR
with open(ZONE_LIST_FILE, "r") as file:
    for line in file:
        zone = line.strip()
        if not zone or zone.startswith("#"):
            continue

        print(f"Performing zone transfer for: {zone}")
        output_file = os.path.join(OUTPUT_DIR, zone)

        try:
            result = subprocess.run(
                ["dig", f"@{dns_server}", "AXFR", zone],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                with open(output_file, "w") as f:
                    f.write(result.stdout)
                print(f"Zone transfer for {zone} completed and saved to {output_file}")
            else:
                print(f"Zone transfer for {zone} failed:\n{result.stderr}")
        except Exception as e:
            print(f"Error transferring zone {zone}: {e}")

