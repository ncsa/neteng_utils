#!/usr/bin/env python3

import os
import shutil
import subprocess
import argparse
import re
from datetime import datetime, timedelta

# ==================== CONFIGURATION ====================

NET_V4 = "0.0.0.0/0
NET_V6 = "::/0"

SCRIPT_PATH = os.path.abspath(__file__)
SCRIPT_PARENT_DIR = os.path.dirname(SCRIPT_PATH)
ROOT_NAME = os.path.basename(os.path.dirname(SCRIPT_PARENT_DIR))
ROOT_DIR = os.path.join("/services", ROOT_NAME)
OUTPUT_DIR = os.path.join(ROOT_DIR, "flows")

FLOWS_DIR = os.path.join("/services", "flowdata")
NFDUMP_BIN = "/usr/bin/nfdump"

DEFAULT_EMAIL = "some@email.com"

# ==================== ARGUMENT PARSING ===================

parser = argparse.ArgumentParser(description="Total Traffic Report")

parser.add_argument('--start', type=str, help='Start date (format: YYYY/MM/DD)')
parser.add_argument('--end', type=str, help='End date (format: YYYY/MM/DD)')

proto_group = parser.add_mutually_exclusive_group()
proto_group.add_argument('-4', dest='ipv4', action='store_true', help='Query IPv4 only')
proto_group.add_argument('-6', dest='ipv6', action='store_true', help='Query IPv6 only')
proto_group.add_argument('-a', dest='all', action='store_true', help='Query both IPv4 and IPv6')

parser.add_argument('--direction', choices=['egress', 'ingress'], required=True,
                    help='Filter direction: egress (src net) or ingress (dst net)')
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

if args.direction == 'egress':
    net_field = 'src'
elif args.direction == 'ingress':
    net_field = 'dst'
else:
    raise SystemExit("Invalid direction")

if args.ipv4:
    net_filter = f"({net_field} net {NET_V4})"
elif args.ipv6:
    net_filter = f"({net_field} net {NET_V6})"
elif args.all:
    net_filter = f"({net_field} net {NET_V4} or {net_field} net {NET_V6})"
else:
    net_filter = f"({net_field} net {NET_V4})"

# ==================== PATH SETUP =========================

router_list = f"exit/{end_dt.year}"
output_file = os.path.join(OUTPUT_DIR, "latest-total.txt")
archive_dir = os.path.join(OUTPUT_DIR, "total-traffic-archive")
latest_archive_dir = os.path.join(OUTPUT_DIR, "latest-archive")

# ==================== STEP 1: RUN NFDUMP =================

nfdump_cmd = [
    NFDUMP_BIN,
    "-R", f"{FLOWS_DIR}/{router_list}",
    "-t", time_win,
    "-s", "as/bytes",
    "-n", "0",
    "-o", "extended",
    net_filter
]

result = subprocess.run(nfdump_cmd, capture_output=True, text=True)

# ==================== STEP 2: PARSE SUMMARY LINE =========

total_bytes = "0"
for line in result.stdout.splitlines():
    if line.startswith("Summary:"):
        match = re.search(r"total bytes:\s+([0-9.]+\s+[KMGTP]?)", line)
        if match:
            total_bytes = match.group(1)
        break

# ==================== STEP 3: WRITE OUTPUT FILE ==========

with open(output_file, "w") as out_f:
    out_f.write("Command run:\n")
    out_f.write("  " + " ".join(nfdump_cmd) + "\n")
    out_f.write(f"Timestamp: {serial}\n\n")
    out_f.write(f"Total {args.direction.upper()} Traffic:\n")
    out_f.write(f"  {total_bytes} bytes\n")

# ==================== STEP 4: ARCHIVE FILES ==============

os.makedirs(archive_dir, exist_ok=True)
shutil.copy(output_file, os.path.join(archive_dir, f"total-{timestamp}.txt"))

os.makedirs(latest_archive_dir, exist_ok=True)
shutil.copy(output_file, os.path.join(latest_archive_dir, f"latest-{timestamp}.txt"))

# ==================== STEP 5: EMAIL REPORT ===============

mail_subject = f"Total Traffic {args.direction.upper()} {end_dt.strftime('%Y-%m-%d %H:%M:%S')}"
mail_recipients = args.email
MAIL_CMD = "/usr/bin/mail"
mail_cmd = f"{MAIL_CMD} -s \"{mail_subject}\" {mail_recipients}"

with open(output_file, "r") as body:
    subprocess.run(mail_cmd, shell=True, stdin=body, check=True)
