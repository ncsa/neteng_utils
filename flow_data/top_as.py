#!/usr/bin/env python3

import os
import csv
import time
import shutil
import subprocess
import argparse
from datetime import datetime, timedelta
from humanize import naturalsize

# ==================== CONFIGURATION ====================

NET_V4 = "0.0.0.0/0"
NET_V6 = ""/0"

SCRIPT_PATH = os.path.abspath(__file__)
SCRIPT_PARENT_DIR = os.path.dirname(SCRIPT_PATH)
ROOT_NAME = os.path.basename(os.path.dirname(SCRIPT_PARENT_DIR))
ROOT_DIR = os.path.join("/services", ROOT_NAME)
OUTPUT_DIR = os.path.join(ROOT_DIR, "flows")

FLOWS_DIR = os.path.join("/services", "flowdata")
NFDUMP_BIN = "/usr/bin/nfdump"
WHOIS_CMD = ["/usr/bin/whois", "-h", "whois.cymru.com"]
TOP_N = 11
CRITERIA = "as/bytes"

DEFAULT_EMAIL = "some@email.com"

# ==================== ARGUMENT PARSING ===================
parser = argparse.ArgumentParser(description="Top AS Talkers Report")

parser.add_argument('--start', type=str, help='Start date (format: YYYY/MM/DD)')
parser.add_argument('--end', type=str, help='End date (format: YYYY/MM/DD)')

proto_group = parser.add_mutually_exclusive_group()
proto_group.add_argument('-4', dest='ipv4', action='store_true', help='Query IPv4 only')
proto_group.add_argument('-6', dest='ipv6', action='store_true', help='Query IPv6 only')
proto_group.add_argument('-a', dest='all', action='store_true', help='Query both IPv4 and IPv6')

parser.add_argument('--direction', choices=['egress', 'ingress'], required=True, help='Filter on egress (src) or ingress (dst) network')
parser.add_argument('--email', type=str, default=DEFAULT_EMAIL,
                    help='Email address to send the report to')

args = parser.parse_args()

# ==================== TIME SETUP =========================
if args.start and args.end:
    try:
        start_dt = datetime.strptime(args.start, '%Y/%m/%d')
        end_dt = datetime.strptime(args.end, '%Y/%m/%d') + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise SystemExit("Invalid date format. Use 'YYYY/MM/DD'")
else:
    now = datetime.now()
    start_dt = now - timedelta(days=1)
    end_dt = now

timestamp = end_dt.strftime("%Y%m%d")
serial = end_dt.strftime("%Y/%m/%d-%H:%M:%S")
time_win = f"{start_dt.strftime('%Y/%m/%d.%H:%M:%S')}-{end_dt.strftime('%Y/%m/%d.%H:%M:%S')}"

# ==================== QUERY FILTER ========================
if args.direction == "egress":
    net_field = "src"
elif args.direction == "ingress":
    net_field = "dst"
else:
    raise SystemExit("Invalid direction. Use 'egress' or 'ingress'.")

if args.ipv4:
    net_filter = f"({net_field} net {NET_V4})"
elif args.ipv6:
    net_filter = f"({net_field} net {NET_V6})"
elif args.all:
    net_filter = f"({net_field} net {NET_V4} or {net_field} net {NET_V6})"
else:
    net_filter = f"({net_field} net {NET_V4})"  # Default to IPv4

# ==================== PATH SETUP =========================
router_list = f"exit/{end_dt.year}"
output_file = os.path.join(OUTPUT_DIR, "top-as-csv.txt")
archive_dir = os.path.join(OUTPUT_DIR, "top-as-archive")
latest_file = os.path.join(OUTPUT_DIR, "latest.txt")
latest_archive_dir = os.path.join(OUTPUT_DIR, "latest-archive")

# ==================== STEP 1: RUN NFDUMP =================
nfdump_cmd = [
    NFDUMP_BIN,
    "-q",
    "-R", ".",
    "-M", f"{FLOWS_DIR}/{router_list}",
    "-n", str(TOP_N),
    "-s", CRITERIA,
    "-t", time_win,
    "-b", "-o", "csv",
    net_filter
]

with open(output_file, "w") as out_f:
    subprocess.run(nfdump_cmd, stdout=out_f, stderr=subprocess.DEVNULL)

# ==================== STEP 2: PARSE CSV ==================
with open(output_file, "r") as csv_file:
    lines = list(csv.reader(csv_file))

# ==================== STEP 3: WRITE HEADER ===============
with open(latest_file, "w") as out_f:
    out_f.write("Command run:\n")
    out_f.write("  " + " ".join(nfdump_cmd) + "\n")
    out_f.write(f"Timestamp: {serial}\n\n")

# ==================== STEP 4: APPEND WHOIS RESULTS =======
with open(latest_file, "a") as out_f:
    for row in lines[2:12]:  # rows 2-11 (skip headers)
        if len(row) < 10:
            continue
        asn = row[4].strip()
        bytes_val = row[9].strip()

        try:
            whois_result = subprocess.check_output(WHOIS_CMD + [f"AS{asn}"], text=True)
        except subprocess.CalledProcessError:
            whois_result = f"AS{asn} lookup failed\n"

        out_f.write(whois_result)
        out_f.write(f"AS{asn}\n")
        out_f.write(f"{naturalsize(float(bytes_val))}\n\n")
        time.sleep(1)

# ==================== STEP 5: ARCHIVE CSV ================
os.makedirs(archive_dir, exist_ok=True)
shutil.move(output_file, os.path.join(archive_dir, os.path.basename(output_file)))

# ==================== STEP 6: ARCHIVE OLD LATEST =========
if os.path.exists(latest_file):
    os.makedirs(latest_archive_dir, exist_ok=True)
    shutil.copy(latest_file, os.path.join(latest_archive_dir, f"latest-{timestamp}.txt"))

# ==================== STEP 7: EMAIL REPORT ===============
mail_subject = f"Top AS Talkers {args.direction.upper()} {end_dt.strftime('%Y-%m-%d')}"
mail_recipients = args.email
MAIL_CMD = "/usr/bin/mail"
mail_cmd = f"{MAIL_CMD} -s \"{mail_subject}\" {mail_recipients}"

with open(latest_file, "r") as body:
    subprocess.run(mail_cmd, shell=True, stdin=body, check=True)
