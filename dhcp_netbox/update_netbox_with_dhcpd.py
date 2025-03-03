#!/usr/bin/python3

import re
import requests

# NetBox Configuration
# Netbox API configuration
NETBOX_URL = 'https://<NETBOX-URL>/api/'
NETBOX_TOKEN = '<API_TOKEN>xA'
CUSTOM_FIELD_NAME = "mac_address"

# File containing ISC dhcpd.conf
DHCPD_CONF_FILE = "dhcpd.conf"

# Regular expression to extract host reservations
HOST_RESERVATION_REGEX = re.compile(
    r"host\s+([\d\.]+)\s*\{.*?fixed-address\s+([\d\.]+);.*?hardware ethernet\s+([0-9A-Fa-f:]+);",
    re.DOTALL
)

# Headers for NetBox API
HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

def parse_dhcpd_conf(file_path):
    """Extracts host reservations (IP and MAC) from dhcpd.conf."""
    with open(file_path, "r") as file:
        content = file.read()

    reservations = {}
    for match in HOST_RESERVATION_REGEX.finditer(content):
        ip_address, fixed_address, mac_address = match.groups()
        reservations[fixed_address] = mac_address.upper()  # Normalize MAC address
    return reservations

def update_netbox_ip(ip, mac):
    """Updates the custom MAC address field for an IP in NetBox."""
    url = f"{NETBOX_URL}ipam/ip-addresses/?address={ip}"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200 and response.json()["count"] > 0:
        ip_id = response.json()["results"][0]["id"]
        update_url = f"{NETBOX_URL}ipam/ip-addresses/{ip_id}/"
        update_data = {"custom_fields": {CUSTOM_FIELD_NAME: mac}}

        update_response = requests.patch(update_url, headers=HEADERS, json=update_data)
        if update_response.status_code == 200:
            print(f"Updated {ip} with MAC {mac}")
        else:
            print(f"Failed to update {ip}: {update_response.text}")
    else:
        print(f"IP {ip} not found in NetBox")

def main():
    reservations = parse_dhcpd_conf(DHCPD_CONF_FILE)
    for ip, mac in reservations.items():
        update_netbox_ip(ip, mac)

if __name__ == "__main__":
    main()

