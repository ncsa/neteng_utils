#!/usr/bin/python3

import os
import dns.zone
import dns.rdatatype
import requests

# Configuration
NETBOX_URL = "https://<netbox-url>/api/plugins/netbox-dns/records/"
NETBOX_ZONES_URL = "https://<netbox-url>/api/plugins/netbox-dns/zones/"
NETBOX_API_TOKEN = "TOKEN"
ZONES_DIRECTORY = "zones"
EXCLUDED_TYPES = {dns.rdatatype.A, dns.rdatatype.AAAA, dns.rdatatype.PTR, dns.rdatatype.SOA}

# Headers for Netbox API
def get_headers():
    return {
        "Authorization": f"Token {NETBOX_API_TOKEN}",
        "Content-Type": "application/json"
    }

def get_zone_id(zone_name):
    response = requests.get(f"{NETBOX_ZONES_URL}?name={zone_name}", headers=get_headers())
    if response.status_code == 200:
        results = response.json().get("results", [])
        if results:
            return results[0]["id"]
    return None

def get_existing_records(zone_id):
    response = requests.get(f"{NETBOX_URL}?zone_id={zone_id}", headers=get_headers())
    if response.status_code == 200:
        return {(rec["name"], rec["type"], rec["value"]) for rec in response.json().get("results", [])}
    return set()

import dns.zone
import dns.rdatatype

def parse_zone_file(zone_file, zone_name):
    records = []
    try:
        zone = dns.zone.from_file(zone_file, zone_name, relativize=False)  # Keep full names
        for name, node in zone.nodes.items():
            for rdataset in node.rdatasets:
                if rdataset.rdtype not in EXCLUDED_TYPES:
                    for rdata in rdataset:
                        record_name = str(name).rstrip('.')  # Remove trailing dot
                        
                        # Handle Apex ("@") domain
                        if record_name == "@":
                            record_name = ""
                        else:
                            # Ensure the record name does not contain the full zone name
                            if record_name.endswith(f".{zone_name.rstrip('.')}"):
                                record_name = record_name.replace(f".{zone_name.rstrip('.')}", "")

                        record_type = dns.rdatatype.to_text(rdataset.rdtype)
                        record_ttl = rdataset.ttl  # Extract TTL

                        # Extract the correct record value
                        if record_type == "MX":
                            record_value = f"{rdata.preference} {str(rdata.exchange).rstrip('.')}."
                        elif record_type == "SRV":
                            record_value = f"{rdata.priority} {rdata.weight} {rdata.port} {str(rdata.target).rstrip('.')}."
                        elif record_type == "CNAME":
                            record_value = f"{str(rdata).rstrip('.')}."
                        elif record_type == "NS":
                            record_value = f"{str(rdata).rstrip('.')}."
#                        elif record_type == "TXT":
#                            record_value = " ".join(rdata.strings)  # TXT records can have multiple parts
                        elif record_type == "SOA":
                            # Ensure MNAME and RNAME do not get double periods
                            mname = str(rdata.mname).rstrip('.')
                            rname = str(rdata.rname).rstrip('.')
                            record_value = f"{mname}. {rname}. {rdata.serial} {rdata.refresh} {rdata.retry} {rdata.expire} {rdata.minimum}"
                        else:
                            record_value = str(rdata)

                        record = {
                            "name": record_name,  # Store only subdomain
                            "type": record_type,
                            "value": record_value,
                            "ttl": record_ttl  # Store TTL from BIND
                        }
                        records.append(record)
    except Exception as e:
        print(f"Error parsing {zone_file}: {e}")
    return records


def upload_to_netbox(records, zone_id):
    existing_records = get_existing_records(zone_id)
    for record in records:
        record_tuple = (record["name"], record["type"], record["value"])
        if record_tuple not in existing_records:
            record["zone"] = zone_id
            try:
                response = requests.post(NETBOX_URL, json=record, headers=get_headers())
                if response.status_code in [200, 201]:
                    print(f"Successfully added record: {record}")
                elif response.status_code == 400 and "There is already an active" in response.text:
                    print(f"Record Already exists in zone {zone_id}: {record}")
                else:
                    print(f"Failed to add record {record}: {response.text}")
            except Exception as e:
                print(f"Error uploading record {record}: {e}")

def main():
    for filename in os.listdir(ZONES_DIRECTORY):
        zone_path = os.path.join(ZONES_DIRECTORY, filename)
        zone_name = filename  # Use filename as the zone name
        zone_id = get_zone_id(zone_name)
        if zone_id:
            records = parse_zone_file(zone_path, zone_name)
            upload_to_netbox(records, zone_id)
        else:
            print(f"Zone {zone_name} not found in Netbox")

if __name__ == "__main__":
    main()

