# Top AS Talkers Report (`top_as.py`)

This script generates a **Top Autonomous Systems (AS) Talkers Report** using NetFlow data.  
It leverages `nfdump` to extract flow data, ranks ASNs by total traffic volume (in bytes), performs WHOIS lookups via Cymru, and emails a formatted summary report.

The report helps identify the largest network talkers (in terms of data volume) across egress or ingress directions for IPv4, IPv6, or both.

---

## Overview

The script performs the following main steps:

1. **Parses command-line arguments** (date range, protocol, direction, email).  
2. **Runs `nfdump`** against the flow archives for the selected time window.  
3. **Parses the CSV output** and enriches each AS with WHOIS data from `whois.cymru.com`.  
4. **Generates a report** file showing AS numbers, organization names, and total traffic volume.  
5. **Archives results** into timestamped folders.  
6. **Emails the report** to a specified recipient.

---

## Configuration

Edit the configuration variables at the top of the script as needed:

```python
NET_V4 = "0.0.0.0/0"           # IPv4 network scope
NET_V6 = "::/0"                # IPv6 network scope
FLOWS_DIR = "/services/flowdata" # Path to nfdump flow archives
NFDUMP_BIN = "/usr/bin/nfdump"  # Path to nfdump binary
WHOIS_CMD = ["/usr/bin/whois", "-h", "whois.cymru.com"]
TOP_N = 11                     # Number of top ASNs to include
CRITERIA = "as/bytes"          # nfdump sort criteria
DEFAULT_EMAIL = "some@email.com"
```

Output files and directories are dynamically built based on the script’s path:

```
/services/<root>/flows/
├── top-as-csv.txt
├── top-as-archive/
├── latest.txt
└── latest-archive/
```

---

## Usage

Run the script manually or via cron with desired filters:

```bash
./top_as.py --direction egress -4 --start 2025/10/21 --end 2025/10/22 --email you@example.com
```

### Arguments

| Argument | Description |
|-----------|-------------|
| `--start` | Start date (YYYY/MM/DD) |
| `--end` | End date (YYYY/MM/DD) |
| `--direction` | Direction filter: `egress` (source) or `ingress` (destination) |
| `-4` | Analyze IPv4 only |
| `-6` | Analyze IPv6 only |
| `-a` | Include both IPv4 and IPv6 |
| `--email` | Email recipient for the final report |

---

## Output Files

- **`top-as-csv.txt`** – Raw CSV output from nfdump (temporary).  
- **`latest.txt`** – Formatted, human-readable AS report (includes WHOIS and byte totals).  
- **`top-as-archive/`** – Historical archive of CSV exports.  
- **`latest-archive/`** – Time-stamped backups of previous reports.  

Each run also includes the `nfdump` command used, timestamp, and byte totals converted to human-readable form using `humanize.naturalsize()`.

---

## Email Report

At the end of the run, the script emails the report using the local mail utility:

```bash
/usr/bin/mail -s "Top AS Talkers EGRESS 2025-10-22" you@example.com < latest.txt
```

You can change the default recipient by editing `DEFAULT_EMAIL` or passing `--email` on the command line.

---

## Example Output

```
Command run:
  /usr/bin/nfdump -q -R . -M /services/flowdata/exit/2025 -n 11 -s as/bytes -t 2025/10/21.00:00:00-2025/10/22.23:59:59 -b -o csv (src net 0.0.0.0/0)
Timestamp: 2025/10/22-23:59:59

AS1234 | EXAMPLE-NETWORK | 235.4 GB
AS5678 | BIGCLOUD INC    | 198.2 GB
AS64500 | UNIVERSITY-OF-NCSA | 135.6 GB
...
```

---

## Dependencies

- Python 3.7+
- [`humanize`](https://pypi.org/project/humanize/)
- `nfdump`, `whois`, and `mail` binaries in `/usr/bin`

Install `humanize` if missing:

```bash
pip install humanize
```

---

## Error Handling

- If `nfdump` or WHOIS lookup fails, the script logs the failure and continues.  
- Invalid date inputs cause an immediate exit with an error message.  
- Reports are skipped for malformed or incomplete CSV rows.

---

## Automation

You can automate daily reports with cron:

```
0 5 * * * /services/flow_data/scripts/top_as.py --direction egress -4 --email neteng@example.com >> /var/log/top_as.log 2>&1
```

This will send a daily top AS talker summary at 5 AM.




# Total Traffic Report (`total_traffic.py`)

This script generates a **Total Network Traffic Report** using NetFlow data from `nfdump`.  
It calculates total data volume (bytes) transferred over a specified time range, filtered by **direction** (egress or ingress) and **protocol** (IPv4, IPv6, or both).  
The output is a concise, timestamped summary emailed to a specified address and archived for recordkeeping.

---

## Overview

The script performs the following main steps:

1. **Parses command-line arguments** for time range, IP version, and traffic direction.  
2. **Runs `nfdump`** to aggregate total bytes transferred across all ASNs.  
3. **Parses the `Summary:` line** from nfdump output to extract the total byte count.  
4. **Writes a summary report** to disk and archives it with timestamps.  
5. **Emails the result** to the configured recipient.

---

## Configuration

At the top of the script, update these constants to match your environment:

```python
NET_V4 = "0.0.0.0/0"             # IPv4 scope
NET_V6 = "::/0"                  # IPv6 scope
FLOWS_DIR = "/services/flowdata" # Directory containing nfdump flow data
NFDUMP_BIN = "/usr/bin/nfdump"   # Path to nfdump binary
DEFAULT_EMAIL = "some@email.com" # Default report recipient
```

Output and archive paths are dynamically generated from the script’s location:

```
/services/<root>/flows/
├── latest-total.txt
├── total-traffic-archive/
└── latest-archive/
```

---

## Usage

Run the script from the command line:

```bash
./total_traffic.py --direction egress -4 --start 2025/10/21 --end 2025/10/22 --email you@example.com
```

### Arguments

| Argument | Description |
|-----------|-------------|
| `--start` | Start date (YYYY/MM/DD) |
| `--end` | End date (YYYY/MM/DD) |
| `--direction` | Direction filter: `egress` (src net) or `ingress` (dst net) |
| `-4` | Analyze IPv4 only |
| `-6` | Analyze IPv6 only |
| `-a` | Include both IPv4 and IPv6 |
| `--email` | Email recipient for the final report |

If no dates are given, the script defaults to the **previous 24 hours**.

---

## Output Files

- **`latest-total.txt`** – The main output file for the most recent report.  
- **`total-traffic-archive/`** – Contains timestamped historical reports.  
- **`latest-archive/`** – Keeps time-stamped backups of recent `latest-total.txt` files.  

Each report includes:

```
Command run:
  /usr/bin/nfdump -R /services/flowdata/exit/2025 -t 2025/10/21.00:00:00-2025/10/22.23:59:59 -s as/bytes -n 0 -o extended (src net 0.0.0.0/0)
Timestamp: 2025/10/22-23:59:59

Total EGRESS Traffic:
  735.2 GB bytes
```

---

## Email Report

At the end of the run, the script sends the report using the local `mail` command:

```bash
/usr/bin/mail -s "Total Traffic EGRESS 2025-10-22 23:59:59" you@example.com < latest-total.txt
```

You can change the recipient with `--email` or modify `DEFAULT_EMAIL`.

---

## Example Output

```
Command run:
  /usr/bin/nfdump -R /services/flowdata/exit/2025 -t 2025/10/21.00:00:00-2025/10/22.23:59:59 -s as/bytes -n 0 -o extended (src net 0.0.0.0/0)
Timestamp: 2025/10/22-23:59:59

Total EGRESS Traffic:
  1.12 TB bytes
```

---

## Dependencies

- Python 3.7+
- `nfdump`
- `mail` utility (e.g., `/usr/bin/mail`)

---

## Error Handling

- Invalid date formats cause the script to exit cleanly with an error message.  
- If `nfdump` execution fails, the output will not contain a summary line.  
- Missing or malformed flow data directories are logged as system errors.

---

## Automation

You can automate daily total traffic reporting with a cron job:

```
0 4 * * * /services/flow_data/scripts/total_traffic.py --direction ingress -4 --email neteng@example.com >> /var/log/total_traffic.log 2>&1
```

This runs every morning at 4 AM and emails the previous day’s total ingress traffic volume.
