#!/usr/bin/python3

import requests

#  NetBox API Configuration
PROD_NETBOX_URL = "https://<NETBOX_URL>"
DEV_NETBOX_URL = "https://<NETBOX_DEV_URL>"
API_ENDPOINTS = {
    "contacts": "/api/plugins/netbox-dns/contacts/",
    "nameservers": "/api/plugins/netbox-dns/nameservers/",
    "registrars": "/api/plugins/netbox-dns/registrars/",
    "views": "/api/plugins/netbox-dns/views/",
    "zones": "/api/plugins/netbox-dns/zones/",
    "prefixes": "/api/plugins/netbox-dns/prefixes/",
}
#  API Tokens
PROD_HEADERS = {
    "Authorization": "Token <API TOKEN>",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

DEV_HEADERS = {
    "Authorization": "Token <API TOKEN>",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# Fields to Exclude Before Sending Data
EXCLUDE_FIELDS = ["id", "url", "display", "created", "last_updated"]

# Matching Fields for Each Endpoint
MATCH_FIELDS = {
    "contacts": "contact_id",
    "nameservers": "name",
    "registrars": "name",
    "views": "name",
    "zones": "name",
    "prefixes": "prefix",
}

def fetch_objects(base_url, headers, endpoint, match_field):
    """Fetch all objects from a NetBox instance."""
    objects = {}
    next_url = f"{base_url}{endpoint}"

    while next_url:
        response = requests.get(next_url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            for obj in data.get("results", []):
                objects[obj[match_field]] = obj  # Store objects by unique match field
            next_url = data.get("next")  # Handle pagination
        else:
            print(f"Error fetching {endpoint} from {base_url}: {response.text}")
            return {}

    return objects

def clean_zone_data(zone, dev_views, dev_nameservers, dev_registrars):
    """Remove unwanted fields and replace references with IDs from Development instance."""
    cleaned_zone = {k: v for k, v in zone.items() if k not in EXCLUDE_FIELDS}

    # Remove required fields from payload if they are None
    required_fields = ["registrar", "registrant", "tech_c", "admin_c", "billing_c"]
    for field in required_fields:
        if cleaned_zone.get(field) is None:
            del cleaned_zone[field]  # Remove from API request entirely


    # Convert `view` reference
    if isinstance(cleaned_zone.get("view"), dict):
        view_name = cleaned_zone["view"]["name"]
        cleaned_zone["view"] = dev_views.get(view_name, {}).get("id")

    # Convert `nameservers` references
    if "nameservers" in cleaned_zone:
        cleaned_zone["nameservers"] = [
            dev_nameservers.get(ns["name"], {}).get("id")
            for ns in cleaned_zone["nameservers"] if ns["name"] in dev_nameservers
        ]

    # Convert `soa_mname` reference
    if isinstance(cleaned_zone.get("soa_mname"), dict):
        soa_name = cleaned_zone["soa_mname"]["name"]
        cleaned_zone["soa_mname"] = dev_nameservers.get(soa_name, {}).get("id")

    # Convert `registrar` reference
    if isinstance(cleaned_zone.get("registrar"), dict):
        registrar_name = cleaned_zone["registrar"]["name"]
        cleaned_zone["registrar"] = dev_registrars.get(registrar_name, {}).get("id")

    return cleaned_zone


    # Ensure required fields are not null
    required_fields = ["registrar", "registrant", "tech_c", "admin_c", "billing_c"]
    for field in required_fields:
        if cleaned_zone.get(field) is None:
            cleaned_zone[field] = None  # Explicitly set to null

    # Convert `view` reference
    if isinstance(cleaned_zone.get("view"), dict):
        view_name = cleaned_zone["view"]["name"]
        cleaned_zone["view"] = dev_views.get(view_name, {}).get("id")

    # Convert `nameservers` references
    if "nameservers" in cleaned_zone:
        cleaned_zone["nameservers"] = [
            dev_nameservers.get(ns["name"], {}).get("id")
            for ns in cleaned_zone["nameservers"] if ns["name"] in dev_nameservers
        ]

    # Convert `soa_mname` reference
    if isinstance(cleaned_zone.get("soa_mname"), dict):
        soa_name = cleaned_zone["soa_mname"]["name"]
        cleaned_zone["soa_mname"] = dev_nameservers.get(soa_name, {}).get("id")

    # Convert `registrar` reference
    if isinstance(cleaned_zone.get("registrar"), dict):
        registrar_name = cleaned_zone["registrar"]["name"]
        cleaned_zone["registrar"] = dev_registrars.get(registrar_name, {}).get("id")

    return cleaned_zone


    # Convert `view` reference
    if isinstance(cleaned_zone.get("view"), dict):
        view_name = cleaned_zone["view"]["name"]
        cleaned_zone["view"] = dev_views.get(view_name, {}).get("id")

    # Convert `nameservers` references
    if "nameservers" in cleaned_zone:
        cleaned_zone["nameservers"] = [
            dev_nameservers.get(ns["name"], {}).get("id")
            for ns in cleaned_zone["nameservers"] if ns["name"] in dev_nameservers
        ]

    # Convert `soa_mname` reference
    if isinstance(cleaned_zone.get("soa_mname"), dict):
        soa_name = cleaned_zone["soa_mname"]["name"]
        cleaned_zone["soa_mname"] = dev_nameservers.get(soa_name, {}).get("id")

    # Convert `registrar` reference
    if isinstance(cleaned_zone.get("registrar"), dict):
        registrar_name = cleaned_zone["registrar"]["name"]
        cleaned_zone["registrar"] = dev_registrars.get(registrar_name, {}).get("id")

    return cleaned_zone

def clean_view_data(view, dev_prefixes):
    """Remove unwanted fields and replace references with IDs from Development instance."""
    cleaned_view = {k: v for k, v in view.items() if k not in EXCLUDE_FIELDS}

    # Convert `prefixes` references
    if "prefixes" in cleaned_view and isinstance(cleaned_view["prefixes"], list):
        cleaned_view["prefixes"] = [
            dev_prefixes.get(prefix["prefix"], {}).get("id")
            for prefix in cleaned_view["prefixes"] if prefix["prefix"] in dev_prefixes
        ]

    return cleaned_view

def sync_objects(endpoint_key, dev_views=None, dev_nameservers=None, dev_registrars=None, dev_prefixes=None):
    """Sync objects from Production to Development."""
    endpoint = API_ENDPOINTS[endpoint_key]
    match_field = MATCH_FIELDS[endpoint_key]

    print(f"Fetching {endpoint_key} from Production...")
    prod_objects = fetch_objects(PROD_NETBOX_URL, PROD_HEADERS, endpoint, match_field)

    print(f"Fetching {endpoint_key} from Development...")
    dev_objects = fetch_objects(DEV_NETBOX_URL, DEV_HEADERS, endpoint, match_field)

    for obj_match, prod_obj in prod_objects.items():
        if endpoint_key == "zones":
            cleaned_obj = clean_zone_data(prod_obj, dev_views, dev_nameservers, dev_registrars)
        elif endpoint_key == "views":
            cleaned_obj = clean_view_data(prod_obj, dev_prefixes)
        else:
            cleaned_obj = {k: v for k, v in prod_obj.items() if k not in EXCLUDE_FIELDS}

        if obj_match not in dev_objects:
            # Create new object in Development
            print(f"➕ Creating {endpoint_key}: {prod_obj['name']} ({obj_match})")
            response = requests.post(f"{DEV_NETBOX_URL}{endpoint}", headers=DEV_HEADERS, json=cleaned_obj)
        else:
            # Update existing object in Development if changed
            dev_obj_id = dev_objects[obj_match]["id"]
            print(f"✏️ Updating {endpoint_key}: {prod_obj['name']} ({obj_match})")
            response = requests.patch(f"{DEV_NETBOX_URL}{endpoint}{dev_obj_id}/", headers=DEV_HEADERS, json=cleaned_obj)

        if response.status_code not in [200, 201]:
            print(f"Error syncing {endpoint_key} {prod_obj['name']}: {response.text}")

if __name__ == "__main__":
    # Fetch related objects from Dev to use for ID mapping
    dev_views = fetch_objects(DEV_NETBOX_URL, DEV_HEADERS, API_ENDPOINTS["views"], "name")
    dev_nameservers = fetch_objects(DEV_NETBOX_URL, DEV_HEADERS, API_ENDPOINTS["nameservers"], "name")
    dev_registrars = fetch_objects(DEV_NETBOX_URL, DEV_HEADERS, API_ENDPOINTS["registrars"], "name")
    dev_prefixes = fetch_objects(DEV_NETBOX_URL, DEV_HEADERS, API_ENDPOINTS["prefixes"], "prefix")

    sync_objects("contacts")
    sync_objects("nameservers")
    sync_objects("registrars")
    sync_objects("views", dev_prefixes=dev_prefixes)
    sync_objects("zones", dev_views, dev_nameservers, dev_registrars)
    sync_objects("prefixes")
