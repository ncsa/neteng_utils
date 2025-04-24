#!/usr/bin/python3

import re
import pynetbox

# NetBox Configuration
NETBOX_URL = 'https://<NETBOX-URL>/api/'
NETBOX_TOKEN = '<API_TOKEN>'
CUSTOM_FIELD_NAME = "mac_address"

# File containing ISC dhcpd.conf
DHCPD_CONF_FILE = "dhcpd.conf"

# Regular expression to extract host reservations
HOST_RESERVATION_REGEX = re.compile(
    r"host\s+([\d\.]+)\s*\{.*?fixed-address\s+([\d\.]+);.*?hardware ethernet\s+([0-9A-Fa-f:]+);",
    re.DOTALL
)

# Initialize pynetbox API instance
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

def parse_dhcpd_conf(file_path):
    """Extracts host reservations (IP and MAC) from dhcpd.conf."""
    with open(file_path, "r") as file:
        content = file.read()

    reservations = {}
    for match in HOST_RESERVATION_REGEX.finditer(content):
        ip_address, fixed_address, mac_address = match.groups()
        # Using fixed_address as the key which is assumed to be the IP used in NetBox
        reservations[fixed_address] = mac_address.upper()  # Normalize MAC address
    return reservations

def update_netbox_ip(ip, mac):
    """Updates the custom MAC address field for an IP in NetBox using pynetbox."""
    ip_obj = nb.ipam.ip_addresses.get(address=ip)
    if ip_obj:
        # Update the custom field for MAC address and save the change
        ip_obj.custom_fields[CUSTOM_FIELD_NAME] = mac
        ip_obj.save()
        print(f"Updated {ip} with MAC {mac}")
    else:
        print(f"IP {ip} not found in NetBox")

def main():
    reservations = parse_dhcpd_conf(DHCPD_CONF_FILE)
    for ip, mac in reservations.items():
        update_netbox_ip(ip, mac)

if __name__ == "__main__":
    main()

