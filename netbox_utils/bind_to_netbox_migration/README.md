# Forward-to-Reverse DNS Consistency Checker (`checks_forward_to_reverse.py`)

This script validates DNS forward and reverse consistency by ensuring that every **A** or **AAAA** record in a BIND zone file has a corresponding **PTR** record that correctly maps back to the same FQDN.

It helps detect mismatches or missing PTRs across large zones by performing live DNS lookups (using dnspython) against an optional specified DNS server.

---

## Overview

The script performs the following main steps:

1. Parses a BIND zone file (supports relative and absolute names).
2. Iterates over A and/or AAAA records in the zone.
3. Performs PTR lookups for each IP address found.
4. Reports:
   - Mismatches (PTR exists but points to a different name)
   - Optionally, missing PTRs (no PTR record found at all)
5. Supports both IPv4 and IPv6 records and optional DNS server targeting.

---

## Configuration and Dependencies

### Dependencies

- Python 3.9+
- [`dnspython`](https://www.dnspython.org/)
- Standard library modules: `argparse`, `socket`, `sys`, `re`, `subprocess`

Install dnspython if needed:

```bash
pip install dnspython
```

### Script Location

You can place this script anywhere, such as:

```
/services/scripts/dns/checks_forward_to_reverse.py
```

and run it directly or via cron.

---

## Usage

```bash
./checks_forward_to_reverse.py /services/bind/zones/example.com.zone example.com. \
  --dns-server 192.0.2.53 --include-missing
```

### Positional Arguments

| Argument | Description |
|-----------|-------------|
| `zone_file` | Path to the BIND zone file (e.g. `/services/bind/zones/example.com.zone`) |
| `origin` | Zone origin (e.g. `example.com.` — trailing dot optional) |

### Optional Arguments

| Option | Description |
|---------|-------------|
| `--dns-server` | IP or hostname of the DNS server to query for PTR lookups (default: system resolver) |
| `--timeout` | Timeout (seconds) for DNS lookups (default: 2.0) |
| `--only-a` | Check only A records |
| `--only-aaaa` | Check only AAAA records |
| `--include-missing` | Include missing PTRs in output (by default, only mismatches are printed) |

---

## Example Output

### Example 1 — Mismatched PTRs
```
❌ PTR mismatches (PTR exists but does not match forward name):
 - web1.example.com. [A] 192.0.2.10 -> PTR(s): wrongname.example.net.
 - api1.example.com. [AAAA] 2001:db8::10 -> PTR(s): api.internal.example.org.
```

### Example 2 — No PTRs Found
```
✅ No PTR mismatches found.

⚠️ No PTR found for these records:
 - db1.example.com. [A] 192.0.2.5 -> (no PTR)
 - mail1.example.com. [AAAA] 2001:db8::25 -> (no PTR)
```

---

## How It Works

- **Forward Records:** The script loads the BIND zone file using `dns.zone.from_file()` and extracts all A and/or AAAA RRs.
- **Reverse Lookups:** Each IP is converted to its reverse DNS form using `dns.reversename.from_address()`, then queried via UDP against:
  - The specified DNS server (`--dns-server`)
  - Or the system resolver if none is given.
- **Normalization:** FQDNs are normalized (lowercased, trailing dot added) for consistent comparison.
- **PTR Matching:** Each PTR record must match the forward FQDN. If not, it is flagged as a mismatch.

---

## Example Scenarios

### Check both A and AAAA records using the default resolver
```bash
./checks_forward_to_reverse.py /services/bind/zones/internal.ncsa.edu internal.ncsa.edu
```

### Check only AAAA records, query a specific DNS server
```bash
./checks_forward_to_reverse.py /services/bind/zones/internal.ncsa.edu internal.ncsa.edu \
  --dns-server 2620:0:c80:4::10 --only-aaaa
```

### Include missing PTRs
```bash
./checks_forward_to_reverse.py /services/bind/zones/internal.ncsa.edu internal.ncsa.edu \
  --include-missing
```

---

## Error Handling

- Invalid or missing zone files produce a clear parsing error message and exit.
- If PTR lookups fail (e.g. NXDOMAIN, timeout, no answer), the script continues gracefully.
- If `getaddrinfo()` fails to resolve a DNS server hostname, the script exits with an explanatory message.
- IPv4 and IPv6 lookups both supported — if `AAAA` is not found, it’s simply skipped.

---

## Summary of Key Functions

| Function | Description |
|-----------|-------------|
| `normalize_fqdn(s)` | Ensures consistent lowercase FQDNs with trailing dots |
| `resolve_dns_server(server)` | Resolves DNS server hostname to an IP address |
| `query_ptr_targets(ip, dns_server_ip, timeout)` | Returns a set of PTR targets for an IP |
| `main()` | Parses arguments, processes the zone, and prints mismatches/missing PTRs |

---

## Example Automation

You can run this nightly to verify forward/reverse consistency across managed zones:

```
0 3 * * * /services/scripts/dns/checks_forward_to_reverse.py /services/bind/zones/internal.ncsa.edu internal.ncsa.edu --dns-server 2620:0:c80:4::10 --include-missing >> /var/log/dns_forward_reverse.log 2>&1
```

---


# Update Managed and Unmanaged Records (`update_managed_unmanaged_records.py`)

This script analyzes DNS forward and reverse zone files, identifies which IPs have valid PTR matches, and synchronizes the results with **NetBox IPAM** and the **NetBox-DNS plugin**.

It distinguishes between **managed (PTR-locked)** and **unmanaged (disable_ptr=True)** DNS records, ensuring that both forward and reverse mappings remain consistent between BIND zone files and NetBox data.

---

## Overview

The script performs two main operations:

1. **PTR-locked synchronization:**
   - For each IP with matching forward and reverse DNS entries (PTR matches forward FQDN):
     - Updates or creates the corresponding IP address object in **NetBox IPAM**.
     - Sets `dns_name` to the verified forward FQDN.
2. **Unmanaged record synchronization:**
   - For forward records without PTR matches or mismatched PTRs:
     - Creates or updates **NetBox-DNS plugin** records with `disable_ptr=True`.
   - Optionally, detects forward records with **no PTRs at all** and reports or fixes them based on provided flags.

This approach ensures that every forward record either:
- Is PTR-locked in IPAM (for exact matches), or  
- Exists as a plugin record (`disable_ptr=True`) for forwards without valid reverse mapping.

---

## Configuration

At the top of the script:

```python
NETBOX_URL   = '<URL>'
NETBOX_TOKEN = '<TOKEN>'
ZONE_FOLDER  = 'zones'
```

Replace these values with your NetBox API endpoint, token, and the directory containing your zone files.

---

## Dependencies

- Python 3.9+
- [`pynetbox`](https://github.com/netbox-community/pynetbox)

Install requirements:

```bash
pip install pynetbox
```

---

## Command-Line Usage

```bash
./update_managed_unmanaged_records.py --zone-folder /services/bind/zones --dry-run
```

### Options

| Option | Description |
|---------|-------------|
| `--zone-folder` | Path to folder containing forward and reverse zone files (default: `zones`) |
| `--dry-run` | Show planned actions without modifying NetBox |
| `--auto-create-no-ptr` | Automatically create or update plugin records for all no-PTR forwards that need action |
| `--log-no-ptr-actions` | Log each no-PTR candidate’s status (`exists_ok`, `needs_create`, etc.) |
| `--no-ptr-only` | Skip PTR-locked pass; process only forward records missing PTRs |

---

## Operation Summary

### Step 1 — Forward Zone Parsing
- Scans all non-reverse zone files for `A` and `AAAA` records.
- Builds a mapping of IP → FQDNs.

### Step 2 — Reverse Zone Parsing
- Scans `in-addr.arpa` and `ip6.arpa` zones for `PTR` records.
- Maps IPs → PTR targets.

### Step 3 — IPAM Updates
- For IPs with PTRs that match a forward FQDN:
  - Updates existing NetBox IP object or creates a new one under the best-matching prefix.
  - Sets `dns_name` to the verified FQDN.

### Step 4 — Plugin Record Management
- For unmatched or missing PTRs:
  - Creates or updates NetBox-DNS plugin records with `disable_ptr=True`.
- If `--no-ptr-only` is set, this step runs alone for all no-PTR forwards.

---

## Example Run

```bash
./update_managed_unmanaged_records.py --zone-folder /services/bind/zones --auto-create-no-ptr
```

Example console output:

```
[12:41:02] === update_managed_records_ptr_locked_plus_plugin.py starting ===
[12:41:02] Zone folder : /services/bind/zones
[12:41:02] Dry run     : False
[12:41:02] Auto no-PTR : True
[12:41:02] NetBox URL  : https://netbox.ncsa.illinois.edu
[12:41:03] Forward A/AAAA discovered: 314
[12:41:04] Reverse PTR unique IPs: 289
[12:41:05] Unique IPs observed in A/AAAA: 312
[12:41:06] [NetBox] Prefixes loaded: v4=44 v6=8
[12:41:06] Evaluating IPs…
[12:41:20] UPDATED IP 141.142.2.11 dns_name: '' -> 'dns1.ncsa.illinois.edu'
[12:41:21] CREATED DNS record web1.ncsa.illinois.edu [A] -> 141.142.2.40 (disable_ptr=True)
...
[12:41:31] === SUMMARY ===
[12:41:31] IPAM created         : 6
[12:41:31] IPAM updated dns_name: 12
[12:41:31] Plugin created       : 9
[12:41:31] Plugin updated (ptr) : 2
[12:41:31] No-PTR needing action: 5
[12:41:32] Done.
```

---

## No-PTR Phase Behavior

When running in **no-PTR mode** (`--no-ptr-only` or default with missing PTRs):

- The script checks each forward record with no reverse match.
- For each, it determines whether a corresponding plugin record already exists:
  - `exists_ok` → already managed (`disable_ptr=True`)
  - `needs_update` → exists but missing `disable_ptr=True`
  - `needs_create` → record missing entirely
- If `--auto-create-no-ptr` is set, these are created or updated automatically.

Example output of a no-PTR summary:

```
[NO-PTR CHECK] web1.internal.ncsa.edu [A] -> 141.142.50.33: needs_create
[NO-PTR - NEED ACTION]
 - 141.142.50.33: candidates=[web1.internal.ncsa.edu [A] (needs_create)]
[NO-PTR] Plugin created: 1
[NO-PTR] Outcome summary: created=1
```

---

## Error Handling

- Gracefully handles missing or unreadable zone files.
- Retries failed NetBox API operations up to three times.
- Skips unknown zones (no matching NetBox-DNS zone entry).
- Warns if an IP address does not match any known prefix in NetBox.
- All dry-run actions are logged but not committed.

---

## Example Automation

Run nightly to keep forward and reverse data synchronized:

```
0 2 * * * /services/scripts/netbox/update_managed_unmanaged_records.py \
  --zone-folder /services/bind/zones \
  --auto-create-no-ptr >> /var/log/netbox/update_records.log 2>&1
```


# Zone Transfer Utility (`zone_transfer.py`)

This script performs **DNS zone transfers (AXFR)** for a list of zones and saves the results as plain text files.  
It uses `dnspython` to query the specified DNS server and outputs each zone’s content for inspection or archival.

---

## Overview

The script:

1. Reads a list of zones from a text file (`zones.txt`).
2. Connects to a DNS server (default: `8.8.8.8` or user-specified via CLI).
3. Attempts a **zone transfer (AXFR)** for each zone.
4. Saves each zone’s records into the `zones/` directory.
5. Logs errors (timeouts, refusals, invalid responses) with clear messages.

This is useful for validating zone accessibility, auditing DNS data, or taking snapshots of zone contents.

---

## Configuration

Edit the variables at the top of the script as needed:

```python
ZONE_LIST_FILE = "zones.txt"           # Input list of zones to transfer
OUTPUT_DIR = "zones"                   # Directory for output zone files
DEFAULT_DNS_SERVER = "8.8.8.8"         # Default DNS server to query (override via CLI)
```

---

## Dependencies

- Python 3.9+
- [`dnspython`](https://www.dnspython.org/)

Install dependencies if necessary:

```bash
pip install dnspython
```

---

## Usage

Run the script directly:

```bash
./zone_transfer.py [dns-server]
```

If no server is provided, it defaults to `8.8.8.8`.

Example:

```bash
./zone_transfer.py ns1.example.com
```

This reads `zones.txt`, performs AXFR queries against `ns1.example.com`, and writes results to the `zones/` directory.

---

## Input Format (`zones.txt`)

Each line should contain one zone name to transfer:

```
example.com
internal.ncsa.edu
reverse.142.141.in-addr.arpa
# Commented lines or blank lines are ignored
```

---

## Output

Each successfully transferred zone is saved to a separate file in the output directory:

```
zones/
├── example.com
├── internal.ncsa.edu
└── reverse.142.141.in-addr.arpa
```

Each file contains resource records in plain text format, one per line:

```
@   3600 IN SOA ns1.example.com. hostmaster.example.com. 2025102301 7200 3600 1209600 3600
www 300 IN A 192.0.2.10
mail 300 IN MX 10 mail.example.com.
```

---

## Error Handling

The script gracefully handles common transfer issues:

| Error | Description |
|--------|-------------|
| `FormError` | Server refused zone transfer |
| `Timeout` | No response from DNS server |
| `BadResponse` | Malformed response received |
| `socket.gaierror` | DNS server hostname could not be resolved |
| General Exception | Any other unexpected failure; stack trace printed |

Example console output:

```
Using DNS server: ns1.example.com
Performing zone transfer for: example.com
Zone transfer for example.com completed and saved to zones/example.com
Performing zone transfer for: internal.ncsa.edu
Timeout: No response from server for internal.ncsa.edu
```

---

## Example Workflow

1. Prepare a list of zones in `zones.txt`.  
2. Ensure you can reach the target DNS server (`dig @ns1.example.com axfr example.com` should succeed).  
3. Run the script:  
   ```bash
   ./zone_transfer.py ns1.example.com
   ```  
4. Review the results under `zones/`.

---

## Notes

- Zone transfer (AXFR) must be **allowed** by the DNS server for each zone.
- If no records are returned, the transfer likely failed due to permissions.
- Useful for troubleshooting, zone validation, or replication verification between DNS servers.
