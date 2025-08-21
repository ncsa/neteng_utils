#!/usr/bin/python3

import os
import dns.zone
import dns.rdatatype
import pynetbox

# Netbox API Endpoint Configuration
NETBOX_URL = "https://<netbox-url>"
NETBOX_API_TOKEN = "TOKEN"
ZONES_DIRECTORY = "zones"

DEFAULT_TTL = 86400

SUPPORTED_TYPES = {
    dns.rdatatype.CNAME,
    dns.rdatatype.TXT,
    dns.rdatatype.MX,
    dns.rdatatype.SRV
}

# Init NetBox API
nb = pynetbox.api(NETBOX_URL, token=NETBOX_API_TOKEN)


def get_zone_id(zone_name):
    zones = nb.plugins.netbox_dns.zones.filter(name=zone_name)
    for zone in zones:
        return zone.id
    return None


def get_existing_records(zone_id):
    records = nb.plugins.netbox_dns.records.filter(zone_id=zone_id)
    return {(r.name, r.type, r.value) for r in records}


def read_zone_with_glue_fix(file_path):
    with open(file_path, "r") as f:
        lines = f.readlines()

    output_lines = []
    last_name = "@"
    has_ttl = any(line.strip().startswith("$TTL") for line in lines)
    if not has_ttl:
        output_lines.append(f"$TTL {DEFAULT_TTL}\n")

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("$"):
            output_lines.append(line)
            continue

        tokens = stripped.split()
        if tokens[0].isdigit() and len(tokens) >= 3 and tokens[1].upper() == "IN":
            output_lines.append(f"{last_name} {line}")
        else:
            if tokens[0].upper() not in {"IN", "$TTL", "$ORIGIN"}:
                last_name = tokens[0]
            output_lines.append(line)

    return "".join(output_lines)


def parse_zone_file(zone_file, zone_name):
    records = []
    zone_base = zone_name.rstrip(".")

    try:
        zone_text = read_zone_with_glue_fix(zone_file)
        zone = dns.zone.from_text(zone_text, origin=dns.name.from_text(zone_name), relativize=False)

        for name, node in zone.nodes.items():
            fqdn = str(name).rstrip(".")

            if fqdn == zone_base or fqdn == "":
                record_name = "@"
            elif fqdn.endswith(f".{zone_base}"):
                record_name = fqdn[:-(len(zone_base) + 1)]
            else:
                record_name = fqdn

            for rdataset in node.rdatasets:
                if rdataset.rdtype not in SUPPORTED_TYPES:
                    continue

                record_type = dns.rdatatype.to_text(rdataset.rdtype)
                record_ttl = rdataset.ttl

                for rdata in rdataset:
                    if record_type == "CNAME":
                        record_value = f"{str(rdata).rstrip('.')}."

                    elif record_type == "TXT":
                        record_value = " ".join([s.decode("utf-8") for s in rdata.strings])

                    elif record_type == "MX":
                        record_value = f"{rdata.preference} {str(rdata.exchange).rstrip('.')}."

                    elif record_type == "SRV":
                        record_value = f"{rdata.priority} {rdata.weight} {rdata.port} {str(rdata.target).rstrip('.')}."

                    else:
                        continue  # safety fallback

                    records.append({
                        "name": record_name,
                        "type": record_type,
                        "value": record_value,
                        "ttl": record_ttl
                    })

    except Exception as e:
        print(f"❌ Error parsing {zone_file}: {e}")

    return records


def upload_to_netbox(records, zone_id):
    existing_records = get_existing_records(zone_id)
    for record in records:
        record_tuple = (record["name"], record["type"], record["value"])
        if record_tuple not in existing_records:
            record["zone"] = zone_id
            try:
                nb.plugins.netbox_dns.records.create(record)
                print(f"✅ Added: {record}")
            except pynetbox.RequestError as e:
                if e.error and "There is already an active" in str(e.error):
                    print(f"⚠️ Record already exists: {record}")
                else:
                    print(f"❌ Failed to add record: {record} — {e.error}")


def main():
    for filename in os.listdir(ZONES_DIRECTORY):
        zone_path = os.path.join(ZONES_DIRECTORY, filename)
        zone_name = filename
        zone_id = get_zone_id(zone_name)

        if not zone_id:
            print(f"🚫 Zone not found in NetBox: {zone_name}")
            continue

        records = parse_zone_file(zone_path, zone_name)
        upload_to_netbox(records, zone_id)


if __name__ == "__main__":
    main()
