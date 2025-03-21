#!/usr/bin/python3

import os
import dns.zone
import dns.rdatatype
import pynetbox

# Netbox API Endpoint Configuration
NETBOX_URL = "https://<netbox-url>"
NETBOX_API_TOKEN = "TOKEN"
ZONES_DIRECTORY = "zones"
EXCLUDED_TYPES = {
    dns.rdatatype.A,
    dns.rdatatype.AAAA,
    dns.rdatatype.PTR,
    dns.rdatatype.SOA,
}

# Initialize pynetbox API client
nb = pynetbox.api(NETBOX_URL, token=NETBOX_API_TOKEN)

def get_zone_id(zone_name):
    results = nb.plugins.netbox_dns.zones.filter(name=zone_name)
    for zone in results:
        return zone.id
    return None

def get_existing_records(zone_id):
    records = nb.plugins.netbox_dns.records.filter(zone_id=zone_id)
    return {(r.name, r.type, r.value) for r in records}

def parse_zone_file(zone_file, zone_name):
    records = []
    try:
        zone = dns.zone.from_file(zone_file, zone_name, relativize=False)
        for name, node in zone.nodes.items():
            for rdataset in node.rdatasets:
                if rdataset.rdtype not in EXCLUDED_TYPES:
                    for rdata in rdataset:
                        record_name = str(name).rstrip('.')

                        if record_name == "@":
                            record_name = ""
                        elif record_name.endswith(f".{zone_name.rstrip('.')}"):
                            record_name = record_name.replace(f".{zone_name.rstrip('.')}", "")

                        record_type = dns.rdatatype.to_text(rdataset.rdtype)
                        record_ttl = rdataset.ttl

                        if record_type == "MX":
                            record_value = f"{rdata.preference} {str(rdata.exchange).rstrip('.')}."
                        elif record_type == "SRV":
                            record_value = f"{rdata.priority} {rdata.weight} {rdata.port} {str(rdata.target).rstrip('.')}."
                        elif record_type in {"CNAME", "NS"}:
                            record_value = f"{str(rdata).rstrip('.')}."
                        elif record_type == "SOA":
                            mname = str(rdata.mname).rstrip('.')
                            rname = str(rdata.rname).rstrip('.')
                            record_value = f"{mname}. {rname}. {rdata.serial} {rdata.refresh} {rdata.retry} {rdata.expire} {rdata.minimum}"
                        else:
                            record_value = str(rdata)

                        records.append({
                            "name": record_name,
                            "type": record_type,
                            "value": record_value,
                            "ttl": record_ttl,
                        })
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
                nb.plugins.netbox_dns.records.create(record)
                print(f"Added: {record}")
            except pynetbox.RequestError as e:
                if e.error and "There is already an active" in str(e.error):
                    print(f"Record already exists in zone {zone_id}: {record}")
                else:
                    print(f"Failed to add record {record}: {e.error}")

def main():
    for filename in os.listdir(ZONES_DIRECTORY):
        zone_path = os.path.join(ZONES_DIRECTORY, filename)
        zone_name = filename  # Use filename as zone name
        zone_id = get_zone_id(zone_name)
        if zone_id:
            records = parse_zone_file(zone_path, zone_name)
            upload_to_netbox(records, zone_id)
        else:
            print(f"Zone not found in NetBox: {zone_name}")

if __name__ == "__main__":
    main()

