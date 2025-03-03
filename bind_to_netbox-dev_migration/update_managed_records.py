#!/usr/bin/python3

import os
import re
import requests
from ipaddress import ip_network, ip_address
import logging

# Netbox API configuration
NETBOX_URL = 'https://<netbox-url.domain/api/'
NETBOX_TOKEN = '<TOKEN>'

# Configure logging
#logging.basicConfig(level=logging.DEBUG)

# NetBox API Configuration
HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
}


# Folder containing BIND zone files
ZONE_FOLDER = "zones"

# Function to parse zone files and extract A (IPv4) and AAAA (IPv6) records
def extract_dns_records(folder):
    print(f"Scanning folder '{folder}' for zone files...")
    records = []

    for filename in os.listdir(folder):
        filepath = os.path.join(folder, filename)
        if os.path.isfile(filepath):  # Ensure it's a file
            print(f"Parsing file: {filename}")
            with open(filepath, "r") as file:
                for line in file:
                    # Match A records (IPv4)
                    match_a = re.match(r"^(\S+)\s+\d+\s+IN\s+A\s+(\d+\.\d+\.\d+\.\d+)", line)
                    if match_a:
                        fqdn, ip = match_a.groups()
                        fqdn = fqdn.rstrip('.')
                        print(f"Found A record (IPv4): {fqdn} -> {ip}")
                        records.append((fqdn, ip))

                    # Match AAAA records (IPv6)
                    match_aaaa = re.match(r"^(\S+)\s+\d+\s+IN\s+AAAA\s+([0-9a-fA-F:]+)", line)
                    if match_aaaa:
                        fqdn, ip = match_aaaa.groups()
                        fqdn = fqdn.rstrip('.')
                        print(f"Found AAAA record (IPv6): {fqdn} -> {ip}")
                        records.append((fqdn, ip))

    return records


def get_prefix_for_ip(ip):
    print(f"Looking up prefix for IP: {ip}")
    prefixes = []
    url = f"{NETBOX_URL}ipam/prefixes/"

    while url:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        prefixes.extend(data.get("results", []))
        url = data.get("next")  # Follow pagination

    # Debug: Print all retrieved prefixes
    print(f"Retrieved prefixes from NetBox: {[p['prefix'] for p in prefixes]}")

    matching_prefixes = []

    for prefix in prefixes:
        network = ip_network(prefix["prefix"], strict=False)
        if ip_address(ip) in network:
            matching_prefixes.append((prefix["prefix"], network.prefixlen))

    if not matching_prefixes:
        print(f"No matching prefix found for IP: {ip}")
        return None

    # Sort prefixes by longest prefix (highest prefix length)
    matching_prefixes.sort(key=lambda x: x[1], reverse=True)
    best_prefix = matching_prefixes[0][0]

    print(f"Most specific prefix found for IP {ip}: {best_prefix}")
    return best_prefix



# Function to update or create an IP address in NetBox
def update_or_create_ip(ip, fqdn):
    print(f"\nProcessing IP: {ip}, FQDN: {fqdn}")
    try:
        # Step 1: Check if the IP exists in NetBox
        print(f"Checking if IP {ip} exists in NetBox...")
        response = requests.get(
            f"{NETBOX_URL}ipam/ip-addresses/",
            params={"address": ip},
            headers=HEADERS
        )
#        print(f"Response status code for IP check: {response.status_code}")
#        print(f"Response content for IP check: {response.text}")
        response.raise_for_status()
        results = response.json().get("results", [])

        # Step 2: Handle existing IP
        if results:
            ip_id = results[0]["id"]
            print(f"IP {ip} exists in NetBox with ID {ip_id}. Preparing to update DNS hostname to {fqdn}.")
            update_data = {"dns_name": fqdn}
            update_response = requests.patch(
                f"{NETBOX_URL}ipam/ip-addresses/{ip_id}/",
                json=update_data,
                headers=HEADERS
            )
#            print(f"Response status code for update: {update_response.status_code}")
#            print(f"Response content for update: {update_response.text}")
            update_response.raise_for_status()
            print(f"Successfully updated IP {ip} with DNS hostname {fqdn}")

        # Step 3: Handle new IP
        else:
            print(f"IP {ip} not found in NetBox. Attempting to create it.")
            prefix = get_prefix_for_ip(ip)
            if not prefix:
                print(f"Error: Could not find prefix for IP {ip}. Skipping...")
                return

            print(f"Prefix for IP {ip} is {prefix}. Creating new entry with DNS hostname {fqdn}.")
            create_data = {"address": f"{ip}/{prefix.split('/')[1]}", "dns_name": fqdn}
            create_response = requests.post(
                f"{NETBOX_URL}ipam/ip-addresses/",
                json=create_data,
                headers=HEADERS
            )
#            print(f"Response status code for creation: {create_response.status_code}")
#            print(f"Response content for creation: {create_response.text}")
            create_response.raise_for_status()
            print(f"Successfully created IP {ip} with DNS hostname {fqdn}")

    except requests.exceptions.RequestException as e:
        print(f"Error occurred while processing IP {ip}, FQDN {fqdn}: {e}")
#        print(f"Exception details: {str(e)}")


# Main function
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

