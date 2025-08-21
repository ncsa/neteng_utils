#!/usr/bin/python3

import os
import re
import pynetbox
from ipaddress import ip_network, ip_address

# Netbox API Endpoint Configuration
NETBOX_URL = 'https://<netbox-url.domain>'
NETBOX_TOKEN = '<TOKEN>'

# Folder containing BIND zone files, don't forget to transfer a new copy
ZONE_FOLDER = "zones"

# Connect to NetBox API using pynetbox
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)


# Parse zone files and extract A (IPv4) and AAAA (IPv6) records
def extract_dns_records(folder):
    print(f"Scanning folder '{folder}' for zone files...")
    records = []

    for filename in os.listdir(folder):
        filepath = os.path.join(folder, filename)
        if os.path.isfile(filepath):
            print(f"Parsing file: {filename}")
            with open(filepath, "r") as file:
                for line in file:
                    match_a = re.match(r"^(\S+)\s+\d+\s+IN\s+A\s+(\d+\.\d+\.\d+\.\d+)", line)
                    if match_a:
                        fqdn, ip = match_a.groups()
                        fqdn = fqdn.rstrip('.')
                        print(f"Found A record (IPv4): {fqdn} -> {ip}")
                        records.append((fqdn, ip))

                    match_aaaa = re.match(r"^(\S+)\s+\d+\s+IN\s+AAAA\s+([0-9a-fA-F:]+)", line)
                    if match_aaaa:
                        fqdn, ip = match_aaaa.groups()
                        fqdn = fqdn.rstrip('.')
                        print(f"Found AAAA record (IPv6): {fqdn} -> {ip}")
                        records.append((fqdn, ip))

    return records

# Find the most specific prefix containing the IP
def get_prefix_for_ip(ip):
    print(f"Looking up prefix for IP: {ip}")
    prefixes = nb.ipam.prefixes.all()

    matching_prefixes = []
    for prefix in prefixes:
        network = ip_network(prefix.prefix, strict=False)
        if ip_address(ip) in network:
            matching_prefixes.append((prefix.prefix, network.prefixlen))

    if not matching_prefixes:
        print(f"No matching prefix found for IP: {ip}")
        return None

    matching_prefixes.sort(key=lambda x: x[1], reverse=True)
    best_prefix = matching_prefixes[0][0]
    print(f"Most specific prefix found for IP {ip}: {best_prefix}")
    return best_prefix


# Update or create the IP address in NetBox
def update_or_create_ip(ip, fqdn):
    print(f"\nProcessing IP: {ip}, FQDN: {fqdn}")
    try:
        ip_obj = nb.ipam.ip_addresses.get(address=ip)

        if ip_obj:
            print(f"IP {ip} exists in NetBox with ID {ip_obj.id}. Updating DNS hostname to {fqdn}.")
            ip_obj.update({'dns_name': fqdn})
            print(f"Successfully updated IP {ip} with DNS hostname {fqdn}")
        else:
            prefix = get_prefix_for_ip(ip)
            if not prefix:
                print(f"Error: Could not find prefix for IP {ip}. Skipping...")
                return

            prefix_len = prefix.split('/')[1]
            print(f"Creating new IP {ip}/{prefix_len} with DNS hostname {fqdn}.")
            nb.ipam.ip_addresses.create({
                "address": f"{ip}/{prefix_len}",
                "dns_name": fqdn
            })
            print(f"Successfully created IP {ip} with DNS hostname {fqdn}")
    except Exception as e:
        print(f"Error occurred while processing IP {ip}, FQDN {fqdn}: {e}")


def main():
    print("Starting the script...")
    try:
        records = extract_dns_records(ZONE_FOLDER)
        print(f"Found {len(records)} DNS records to process.\n")
        for fqdn, ip in records:
            update_or_create_ip(ip, fqdn)
    except Exception as e:
        print(f"Error: {e}")
    print("Script finished.")


if __name__ == "__main__":
    main()

