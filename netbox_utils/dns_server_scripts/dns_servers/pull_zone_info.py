#!/usr/bin/env python3

import pynetbox

NETBOX_URL = "<URL>"
NETBOX_TOKEN = "<TOKEN>"

# Output paths
ZONE_INTERNAL_OUTPUT = "dynamic_configs/zone_internal.conf"
VIEW_INTERNAL_OUTPUT = "dynamic_configs/view_internal.conf"
ZONE_EXTERNAL_OUTPUT = "dynamic_configs/zone_external.conf"
VIEW_EXTERNAL_OUTPUT = "dynamic_configs/view_external.conf"

# Initialize pynetbox
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
zones_api = nb.plugins.netbox_dns.zones

# Output containers
zone_internal_lines = []
view_internal_lines = []
zone_external_lines = []
view_external_lines = []

offset = 0
limit = 50

while True:
    zones_page = zones_api.all(offset=offset, limit=limit)
    if not zones_page:
        break

    for zone in zones_page:
        tags = getattr(zone, "tags", [])
        tag_slugs = {getattr(tag, "slug", "") for tag in tags}

        if getattr(zone.view, "name", None) == "default" and zone.status == "active":
            zone_block = f'''zone "{zone.name}" {{
  type slave;
  file "/services/bind/zonefiles/public/{zone.name}";
  masters {{
         141.142.141.147;
  }};
  zone-statistics yes;
}};'''

            view_block = f'''zone "{zone.name}" {{
  in-view "default";
}};'''

            if "zone_internal" in tag_slugs:
                zone_internal_lines.append(zone_block)
                view_internal_lines.append(view_block)

            if "zone_external" in tag_slugs:
                zone_external_lines.append(zone_block)
                view_external_lines.append(view_block)

    offset += limit

# Write internal files
with open(ZONE_INTERNAL_OUTPUT, "w") as f:
    f.write("\n".join(zone_internal_lines) + "\n")

with open(VIEW_INTERNAL_OUTPUT, "w") as f:
    f.write("\n".join(view_internal_lines) + "\n")

# Write external files
with open(ZONE_EXTERNAL_OUTPUT, "w") as f:
    f.write("\n".join(zone_external_lines) + "\n")

with open(VIEW_EXTERNAL_OUTPUT, "w") as f:
    f.write("\n".join(view_external_lines) + "\n")

print(f"Written {len(zone_internal_lines)} zones to {ZONE_INTERNAL_OUTPUT}")
print(f"Written {len(view_internal_lines)} zones to {VIEW_INTERNAL_OUTPUT}")
print(f"Written {len(zone_external_lines)} zones to {ZONE_EXTERNAL_OUTPUT}")
print(f"Written {len(view_external_lines)} zones to {VIEW_EXTERNAL_OUTPUT}")
