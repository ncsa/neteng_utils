#!/usr/bin/python3

import requests

# PowerDNS API Configuration
API_URL = "http://<powerdns-api>:8081/api/v1/servers/localhost/zones"  # Update with your PowerDNS API URL
API_KEY = "<API KEY>"  # Replace with your actual API key

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def get_zones():
    """Fetch all zones from PowerDNS."""
    response = requests.get(API_URL, headers=HEADERS)

    if response.status_code == 200:
        zones = response.json()
        if not zones:
            print("No zones found.")
        else:
            print(f"Found {len(zones)} zones.")
        return zones
    else:
        print(f"Error fetching zones: {response.status_code} - {response.text}")
        return []

def delete_records(zone_name):
    """Delete all records in a zone except SOA and NS."""
    zone_url = f"{API_URL}/{zone_name}"
    response = requests.get(zone_url, headers=HEADERS)

    if response.status_code != 200:
        print(f"Failed to fetch records for {zone_name}: {response.status_code} - {response.text}")
        return

    zone_data = response.json()
    rrsets = []

    # Identify records to delete (everything except SOA and NS)
    for rrset in zone_data.get("rrsets", []):
        if rrset["type"] not in ["SOA", "NS"]:
            rrsets.append({
                "name": rrset["name"],
                "type": rrset["type"],
                "changetype": "DELETE",
                "records": []
            })

    if not rrsets:
        print(f"No records to delete in zone: {zone_name}")
        return

    # Send deletion request
    payload = {"rrsets": rrsets}
    update_response = requests.patch(zone_url, json=payload, headers=HEADERS)

    if update_response.status_code == 204:
        print(f"Successfully deleted records from {zone_name}")
    else:
        print(f"Failed to delete records from {zone_name}: {update_response.status_code} - {update_response.text}")

def main():
    zones = get_zones()
    if not zones:
        return

    for zone in zones:
        print(f"Processing zone: {zone['id']}")
        delete_records(zone["id"])

if __name__ == "__main__":
    main()

