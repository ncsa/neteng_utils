#!/bin/bash

# Define the input file, output directory, and DNS server
ZONE_LIST_FILE="zones.txt"
OUTPUT_DIR="zones"
DNS_SERVER="<dns.server.com" # Default DNS server (change as needed)

# Check if a DNS server is provided as an argument
if [[ -n "$1" ]]; then
    DNS_SERVER="$1"
fi

echo "Using DNS server: $DNS_SERVER"

# Ensure the output directory exists
mkdir -p "$OUTPUT_DIR"

# Check if the zone list file exists
if [[ ! -f "$ZONE_LIST_FILE" ]]; then
    echo "Error: $ZONE_LIST_FILE does not exist."
    exit 1
fi

# Perform zone transfers for each zone in the file
while read -r zone; do
    # Skip empty lines or lines starting with #
    [[ -z "$zone" || "$zone" == \#* ]] && continue

    echo "Performing zone transfer for: $zone"

    # Save the zone transfer output to a file in the zones directory
    output_file="$OUTPUT_DIR/${zone}"
    dig @"$DNS_SERVER" AXFR "$zone" > "$output_file"

    if [[ $? -eq 0 ]]; then
        echo "Zone transfer for $zone completed and saved to $output_file"
    else
        echo "Zone transfer for $zone failed."
    fi
done < "$ZONE_LIST_FILE"
