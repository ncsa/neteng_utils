#!/usr/bin/python3

import pynetbox

# Configuration
PROD_NETBOX_URL = "https://<NETBOX_URL>"
DEV_NETBOX_URL = "https://<NETBOX-DEV_URL"

PROD_API_TOKEN = "<API TOKEN>"
DEV_API_TOKEN = "<API TOKEN>"

# Connect to Netbox APIs
nb_prod = pynetbox.api(PROD_NETBOX_URL, token=PROD_API_TOKEN)
nb_dev = pynetbox.api(DEV_NETBOX_URL, token=DEV_API_TOKEN)


def cleanup_vlans():
    """Delete VLANs in Development that are not in Production."""
    prod_vlans = {vlan.vid for vlan in nb_prod.ipam.vlans.all()}
    dev_vlans = nb_dev.ipam.vlans.all()

    for vlan in dev_vlans:
        if vlan.vid not in prod_vlans:
            nb_dev.ipam.vlans.delete([vlan.id])
            print(f"Deleted VLAN {vlan.vid} ({vlan.name}) from Dev")


def cleanup_prefixes():
    """Delete Prefixes in Development that are not in Production."""
    prod_prefixes = {prefix.prefix for prefix in nb_prod.ipam.prefixes.all()}
    dev_prefixes = nb_dev.ipam.prefixes.all()

    for prefix in dev_prefixes:
        if prefix.prefix not in prod_prefixes:
            nb_dev.ipam.prefixes.delete([prefix.id])
            print(f"Deleted Prefix {prefix.prefix} from Dev")


def sync_vlans():
    """Sync VLANs from Production to Development."""
    prod_vlans = nb_prod.ipam.vlans.all()
    dev_vlans = {vlan.vid: vlan for vlan in nb_dev.ipam.vlans.all()}

    for vlan in prod_vlans:
        if vlan.vid in dev_vlans:
            # VLAN exists, update if necessary
            dev_vlan = dev_vlans[vlan.vid]
            if (dev_vlan.name != vlan.name or dev_vlan.description != vlan.description or
                dev_vlan.status.value != vlan.status.value if vlan.status else None):

                update_data = {
                    "id": dev_vlan.id,
                    "name": vlan.name,
                    "description": vlan.description,
                    "status": vlan.status.value if vlan.status else None
                }
                nb_dev.ipam.vlans.update([update_data])
                print(f"Updated VLAN {vlan.vid} in Dev (Name: {vlan.name}, Description: {vlan.description})")
            else:
                print(f"VLAN {vlan.vid} already exists in Dev with correct data. Skipping...")
            continue

        # Create VLAN in Dev
        new_vlan = {
            "vid": vlan.vid,
            "name": vlan.name,
            "description": vlan.description,
            "tenant": vlan.tenant.id if vlan.tenant else None,
            "status": vlan.status.value if vlan.status else None,
            "site": getattr(vlan, "site", None) and vlan.site.id,
            "group": vlan.group.id if vlan.group else None,
            "role": vlan.role.id if vlan.role else None,
            "tags": [tag.id for tag in vlan.tags] if vlan.tags else [],
        }
        nb_dev.ipam.vlans.create(new_vlan)
        print(f"Created VLAN {vlan.vid} in Dev")


def sync_prefixes():
    """Sync Prefixes from Production to Development."""
    prod_prefixes = nb_prod.ipam.prefixes.all()
    dev_prefixes = {prefix.prefix: prefix for prefix in nb_dev.ipam.prefixes.all()}

    # Get VLANs in both systems
    prod_vlans = {vlan.id for vlan in nb_prod.ipam.vlans.all()}  # Set of valid VLAN IDs in Prod
    dev_vlans = {vlan.vid: vlan.id for vlan in nb_dev.ipam.vlans.all()}  # Map Dev VLAN VIDs to their IDs

    for prefix in prod_prefixes:
        vlan_id = prefix.vlan.id if prefix.vlan else None

        # If VLAN exists in Production, map it to the correct Dev VLAN ID
        if vlan_id in prod_vlans:
            mapped_vlan = dev_vlans.get(vlan_id, None)  # Map Prod VLAN ID to Dev VLAN ID
        else:
            print(f"Warning: Prefix {prefix.prefix} references VLAN {vlan_id}, which does not exist in Prod. Removing VLAN reference.")
            mapped_vlan = None  # Remove invalid VLAN reference

        if prefix.prefix in dev_prefixes:
            # Prefix exists, update if necessary
            dev_prefix = dev_prefixes[prefix.prefix]

            # Ensure dev_prefix.vlan is not None before accessing .id
            dev_vlan_id = dev_prefix.vlan.id if dev_prefix.vlan else None

            if dev_prefix.description != prefix.description or dev_prefix.status.value != prefix.status.value or dev_vlan_id != mapped_vlan:
                update_data = {
                    "id": dev_prefix.id,
                    "description": prefix.description,
                    "status": prefix.status.value if prefix.status else None,
                    "vlan": mapped_vlan  # Use correct VLAN or None
                }
                nb_dev.ipam.prefixes.update([update_data])
                print(f"Updated Prefix {prefix.prefix} in Dev (Description: {prefix.description})")
            else:
                print(f"Prefix {prefix.prefix} already exists in Dev with correct data. Skipping...")
            continue

        # Create Prefix in Dev
        new_prefix = {
            "prefix": prefix.prefix,
            "description": prefix.description,
            "tenant": prefix.tenant.id if prefix.tenant else None,
            "status": prefix.status.value if prefix.status else None,
            "site": getattr(prefix, "site", None) and prefix.site.id,  # Handles missing `site`
            "vlan": mapped_vlan,  # Use the mapped VLAN or None
            "role": prefix.role.id if prefix.role else None,
            "is_pool": prefix.is_pool,
            "tags": [tag.id for tag in prefix.tags] if prefix.tags else [],
        }
        nb_dev.ipam.prefixes.create(new_prefix)
        print(f"Created Prefix {prefix.prefix} in Dev")


if __name__ == "__main__":
    print("Cleaning up VLANs in Dev that are not in Prod...")
    cleanup_vlans()

    print("Cleaning up Prefixes in Dev that are not in Prod...")
    cleanup_prefixes()

    print("Syncing VLANs from Production to Development...")
    sync_vlans()

    print("Syncing Prefixes from Production to Development...")
    sync_prefixes()

    print("Sync completed successfully!")

