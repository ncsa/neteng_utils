#!/usr/bin/env python3

import pynetbox

NETBOX_URL = "<URL>"
NETBOX_TOKEN = "<TOKEN>"

# Output paths
DEFAULT_ZONE_OUTPUT = "/services/bind/config/active_zones.conf"
SPLIT_HORIZON_ZONE_OUTPUT = "/services/bind/config/split-horizon_zones.conf"

# Initialize pynetbox
nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)

# Plugin endpoint
zones_api = nb.plugins.netbox_dns.zones

default_zone_lines = []
split_zone_lines = []

# Fetch all zones (pynetbox will paginate internally)
for zone in zones_api.all():
    view_name = getattr(zone.view, "name", None)
    status = getattr(zone, "status", None)

    if status != "active":
        continue

    if view_name == "default":
        default_zone_lines.append(
            f'''zone "{zone.name}" {{
  type master;
  file "/services/bind/zonefiles/default/{zone.name}";
  notify yes;
}};'''
        )
    elif view_name == "split-horizon":
        split_zone_lines.append(
            f'''zone "{zone.name}" {{
  type master;
  file "/services/bind/zonefiles/split-horizon/{zone.name}";
  notify yes;
}};'''
        )

# Write outputs
with open(DEFAULT_ZONE_OUTPUT, "w") as f:
    f.write("\n".join(default_zone_lines))

with open(SPLIT_HORIZON_ZONE_OUTPUT, "w") as f:
    f.write("\n".join(split_zone_lines))

print(f'Written {len(default_zone_lines)} zones to {DEFAULT_ZONE_OUTPUT}')
print(f'Written {len(split_zone_lines)} zones to {SPLIT_HORIZON_ZONE_OUTPUT}')
