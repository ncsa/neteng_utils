#!/usr/bin/python3

import pynetbox
import json
import re
import subprocess

# NetBox API setup
url = "https://<NETBOX-URL"
token = "API TOKEN"
nb = pynetbox.api(url, token=token)

# Function to sanitize prefix for use in filenames
def sanitize_prefix(prefix):
    # Replace `/` with `_` to make the prefix safe for filenames
    return re.sub(r"[/:]", "_", prefix)

# Function to process reservations for a given list of prefixes
def process_reservations(prefixes, family):
    for prefix in prefixes:
        # Fetch all IP addresses within the prefix
        ip_addresses = nb.ipam.ip_addresses.filter(parent=prefix.prefix)

        # Initialize the list of reservations
        reservations = []

        # Process each IP address
        for address in ip_addresses:
            reservation = {}
            try:
                # Split the address since NetBox stores it in CIDR notation
                splitadd = address.address.split("/")
                reservation['ip-address'] = splitadd[0]

                # Access the MAC address from the custom field
                mac_address = address.custom_fields.get('mac_address')

                # Skip if there's no MAC address
                if not mac_address:
                    continue

                reservation['hw-address'] = mac_address

                # Use the hostname from the DNS name field or a placeholder if missing
                reservation['hostname'] = address.dns_name if address.dns_name else "unknown"

                # Append the reservation to the list
                reservations.append(reservation)

            except Exception as e:
                # Catch any unexpected errors and log them
                print(f"Error processing address {address.address}: {e}")

        # Generate a unique filename for the prefix
        sanitized_prefix = sanitize_prefix(prefix.prefix)
        file_suffix = "v4" if family == 4 else "v6"
        output_file = f"/etc/kea/host_reservations/kea-dhcp-{file_suffix}-{sanitized_prefix}-reservations.json"

        # Write the reservations to the JSON file
        with open(output_file, 'w') as outfile:
            json.dump(reservations, outfile, indent=4)
        print(f"Reservations for prefix {prefix.prefix} written to {output_file}")

# Retrieve IPv4 prefixes with the "office_dhcp" tag
ipv4_prefixes = nb.ipam.prefixes.filter(tag="office_dhcp", family=4)

# Process IPv4 reservations
process_reservations(ipv4_prefixes, family=4)

# Retrieve IPv6 prefixes with the "office_dhcp" tag
ipv6_prefixes = nb.ipam.prefixes.filter(tag="office_dhcp", family=6)

# Process IPv6 reservations
process_reservations(ipv6_prefixes, family=6)

# API call to reload DHCP configurations
try:
    # Reload DHCP4 configuration
    dhcp4_response = subprocess.run(
        [
            "curl", "-X", "POST",
            "-H", "Content-Type: application/json",
            "-d", '{"command": "config-reload", "service": ["dhcp4"]}',
            "localhost:8000"
        ],
        check=True,
        text=True,
        capture_output=True
    )
    print(f"DHCP4 reload response: {dhcp4_response.stdout}")

    # Reload DHCP6 configuration
    dhcp6_response = subprocess.run(
        [
            "curl", "-X", "POST",
            "-H", "Content-Type: application/json",
            "-d", '{"command": "config-reload", "service": ["dhcp6"]}',
            "localhost:8000"
        ],
        check=True,
        text=True,
        capture_output=True
    )
    print(f"DHCP6 reload response: {dhcp6_response.stdout}")

except subprocess.CalledProcessError as e:
    print(f"Error reloading configurations: {e.stderr}")
