# Domain Registration Utilities

This repo contains python scripts for tracking domain registrations and storing them in the Netbox-DNS plugin:

- **`update_registration_whois.py`** — fetches WHOIS data for a list of domains and updates a local state store (file/DB) with normalized fields such as *expiration date*, *domain status*, and *registry domain ID*.
- **`domain_expiration_notice.py`** — reads the stored expiration data and emails reminder notices at specific day offsets prior to each domain’s expiration.

---

## 1) `update_registration_whois.py`

### What it does
- Executes the system `whois` command for each domain.
- Parses WHOIS fields
  - `expiration_date` (ISO 8601 in UTC when present)
  - `domain_status` (list of status strings, e.g., `clientTransferProhibited`)
  - `registry_domain_id` (string when available)
- Updates the Netbox-DNS plugin with the parsed data.  This is stores in the Registrar tab for each zone file.

### Requirements
- `whois` package must be installed on the system

### Output schema (normalized)
The script normalizes key values before writing to the state store. Typical record:
```json
{
  "domain": "<yourdomain.com>",
  "queried_at": "2025-08-20T14:47:49Z",
  "expiration_date": "2025-08-25T00:00:00Z",
  "domain_status": ["clientDeleteProhibited", "clientRenewProhibited", "clientTransferProhibited", "clientUpdateProhibited"],
  "registry_domain_id": "<domain_ID",
  "raw_whois_excerpt": "Registry Expiry Date: 2025-08-25T23:59:59Z\nDomain Status: clientTransferProhibited ..."
}
```
### Known WHOIS variants handled
- Expiration fields: `Registry Expiry Date`, `Expiry Date`, `Expiration Date`, `paid-till`, `renewal date`, etc.
- Status fields:
  - `.com/.net` usually multiple `Domain Status:` lines
  - `.us` often `status: ACTIVE`
  - Other TLDs may lowercase/uppercase or use localized labels
- The parser normalizes casing and trims extra text (e.g., URL hints after status).

### Notes
```Netbox
# This script should be installed in Netbox under "Customization" > "Scripts"


## 2) `domain_expiration_notice.py`

### What it does
- Reads the date produced by `update_registration_whois.py`.
- Compares each domain’s `expiration_date` to **today**.
- If an expiration falls at one of your alert windows, it sends an email.
- Default alert windows (days before expiry): **90, 60, 30, 15, 10, 5, 4, 3, 2, 1**.

### Email delivery
Supports standard SMTP over TLS. You’ll provide SMTP host/port/credentials or use a relay that allows unauthenticated submissions from your host.

### Example usage
```Netbox
# This script should be installed in Netbox under "Customization" > "Scripts"

### Email content
- Subject: `Domain expiring in N days: <domain>`
- Body includes:
  - Domain
  - Days until expiration
  - Expiration date (UTC and local)
  - Current status list (when available)
  - Helpful guidance (e.g., where to renew)
