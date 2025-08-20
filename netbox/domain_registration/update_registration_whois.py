from extras.scripts import Script, StringVar
from datetime import datetime, date, timezone as dt_timezone
import subprocess
import re

from netbox_dns.models import Zone, Contact  # NEW: Contact FKs

# Registry WHOIS overrides (thin registries / authoritative servers)
WHOIS_OVERRIDES = {
    "org": "whois.pir.org",
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "us":  "whois.nic.us",
}


class NamecheapWhoisSync(Script):
    class Meta:
        name = "Sync expiration, status & contacts via WHOIS (Namecheap)"
        description = "For zones with registrar=Namecheap, update expiration_date, domain_status, registry_domain_id, and admin/tech/billing contacts from WHOIS."
        commit_default = False  # requires Commit checkbox in the UI

    registrar_name = StringVar(
        description="Registrar display name to filter on",
        default="Namecheap",
        required=True,
    )

    # ---------------- helpers ---------------- #

    def _tld_of(self, domain: str) -> str:
        d = domain.lower().strip(".")
        return d.rsplit(".", 1)[-1] if "." in d else ""

    def _run_whois_server(self, server: str | None, domain: str) -> str | None:
        """
        Run WHOIS against a specific server (if given).
        """
        cmd = ["whois"]
        if server:
            cmd += ["-h", server]
        cmd += [domain]

        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if cp.returncode == 0 and cp.stdout:
                return cp.stdout
            return None
        except FileNotFoundError:
            self.log_failure("whois binary not found in the NetBox runtime. Install it on the host/container.")
            return None
        except Exception as e:
            self.log_warning(f"WHOIS error for {domain} (server={server or 'default'}): {e!r}")
            return None

    def _run_whois_bundle(self, domain: str) -> tuple[str | None, str | None]:
        """
        Do a registry WHOIS first (for .org/.com/.net/.us we hit the authoritative server),
        then, if we can find a 'Registrar WHOIS Server', query that too for richer data
        (contacts, etc.). Returns (registry_text, registrar_text).
        """
        tld = self._tld_of(domain)
        registry_server = WHOIS_OVERRIDES.get(tld)
        registry_text = self._run_whois_server(registry_server, domain)

        registrar_text = None
        if registry_text:
            m = re.search(r"^\s*Registrar WHOIS Server\s*:\s*(\S+)\s*$", registry_text, re.IGNORECASE | re.MULTILINE)
            if m:
                registrar = m.group(1).strip().rstrip(".")
                registrar_text = self._run_whois_server(registrar, domain)
        else:
            # Fallback: try default WHOIS; sometimes registrar output comes directly
            registrar_text = self._run_whois_server(None, domain)

        return registry_text, registrar_text

    def _parse_expiry(self, text: str) -> date | None:
        """
        Parse expiration from WHOIS text. Accept common keys and allow leading whitespace.
        Returns a date object (UTC).
        """
        keys = [
            r"Registry Expiry Date",
            r"Registrar Registration Expiration Date",
            r"Expiration Date",
            r"Expiry Date",
            r"Expiry date",
            r"paid-till",
            r"free-date",
        ]
        for key in keys:
            m = re.search(rf"^\s*{key}\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
            if not m:
                continue
            raw = m.group(1).strip()
            # strip comments / trailing junk
            raw = re.split(r"\s{2,}|\s\(|\s#|;", raw)[0].strip()

            # Try strict-ish ISO first (handles ...Z)
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                # Relaxed: pull YYYY-MM-DD with optional time
                m2 = re.search(r"(\d{4}-\d{2}-\d{2})(?:[T\s](\d{2}:\d{2}:\d{2}))?", raw)
                if not m2:
                    continue
                iso = m2.group(1) + ("T" + m2.group(2) + "+00:00" if m2.group(2) else "T00:00:00+00:00")
                try:
                    dt = datetime.fromisoformat(iso)
                except Exception:
                    continue

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            else:
                dt = dt.astimezone(dt_timezone.utc)
            return dt.date()
        return None

    def _normalize_status(self, token: str) -> str | None:
        """
        Normalize WHOIS status tokens:
        - EPP-like camelCase (e.g., clientTransferProhibited) -> keep as-is
        - Uppercase registrar codes (ACTIVE, OK, PENDINGDELETE, etc.) -> lowercase
        - Otherwise, return trimmed token
        """
        t = token.strip()
        t = re.sub(r"^[^\w-]+|[^\w-]+$", "", t)
        if not t:
            return None
        if re.fullmatch(r"[A-Z_]+", t):
            return t.lower()
        return t

    def _parse_status(self, text: str) -> list[str]:
        """
        Pull statuses from WHOIS, supporting both registry and registrar formats.
        """
        out = []

        # Registry-style (multiple lines possible)
        for m in re.finditer(r"^\s*Domain\s+Status:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE):
            line = m.group(1).strip()
            token = line.split()[0] if line else ""
            norm = self._normalize_status(token)
            if norm:
                out.append(norm)

        # Generic registrar lines (often single token like ACTIVE)
        for pat in (r"^\s*status:\s*(\S+)", r"^\s*Status:\s*(\S+)"):
            for m in re.finditer(pat, text, flags=re.IGNORECASE | re.MULTILINE):
                norm = self._normalize_status(m.group(1))
                if norm:
                    out.append(norm)

        # De-dup preserve order
        seen, dedup = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                dedup.append(s)
        return dedup

    def _parse_registry_domain_id(self, text: str) -> str | None:
        """
        Extract 'Registry Domain ID' (common on registry WHOIS).
        """
        m = re.search(r"^\s*Registry\s+Domain\s+ID\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
        # fallback keys sometimes seen
        m = re.search(r"^\s*Domain\s+ID\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else None

    def _parse_contact_role(self, text: str, role: str) -> dict | None:
        """
        Parse a single contact role (admin/tech/billing) from WHOIS text.
        Returns dict like {"name":..., "org":..., "email":...} when something is found.
        """
        role_specs = {
            "admin":   [r"Admin", r"Administrative\s+Contact"],
            "tech":    [r"Tech", r"Technical\s+Contact"],
            "billing": [r"Billing", r"Billing\s+Contact"],
        }
        keys_name = [r"Name", r"Contact\s+Name"]
        keys_org  = [r"Organization", r"Organisation", r"Org"]
        keys_mail = [r"Email", r"E-mail"]

        if role not in role_specs:
            return None

        prefixes = role_specs[role]
        found = {}

        def first_match(patterns):
            for pfx in prefixes:
                for key in patterns:
                    m = re.search(rf"^\s*{pfx}\s+{key}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
                    if m:
                        return m.group(1).strip()
            return None

        email = first_match(keys_mail)
        name  = first_match(keys_name)
        org   = first_match(keys_org)

        # Some registrars just do "Role Email:" without space (e.g., AdminEmail)
        if not email:
            for pfx in prefixes:
                m = re.search(rf"^\s*{pfx}\s*Email\s*:\s*(\S+)$", text, re.IGNORECASE | re.MULTILINE)
                if m:
                    email = m.group(1).strip()
                    break

        if not any([email, name, org]):
            return None

        return {"email": (email or "").lower(), "name": name or "", "org": org or ""}

    def _ensure_contact(self, role_label: str, info: dict | None, commit: bool) -> Contact | None:
        """
        Ensure a netbox_dns.Contact for the given role. Minimal requirement: name.
        If email parsed, prefer match/create by email; otherwise by name.
        """
        if not info:
            return None

        # Build a name we can always save
        name = info.get("name") or info.get("org") or f"{role_label.title()} contact (WHOIS)"
        email = info.get("email") or None

        existing = None
        if email:
            existing = Contact.objects.filter(email__iexact=email).first()
        if not existing:
            existing = Contact.objects.filter(name=name).first()

        if existing:
            if email and getattr(existing, "email", None) != email:
                if commit:
                    existing.email = email
                    existing.save()
                    self.log_info(f'Updated contact "{existing.name}" email -> {email}')
                else:
                    self.log_info(f'[DRY-RUN] Would update contact "{existing.name}" email -> {email}')
            return existing

        if not commit:
            self.log_info(f'[DRY-RUN] Would create contact "{name}"{f" <{email}>" if email else ""}.')
            return Contact(name=name, email=email or "")

        c = Contact.objects.create(name=name, email=email or "")
        self.log_success(f'Created contact "{c.name}"{f" <{email}>" if email else ""}.')
        return c

    # ---------------- main ---------------- #

    def run(self, data, commit):
        registrar_display = data["registrar_name"].strip()

        zones = Zone.objects.filter(
            active=True,
            registrar__name__iexact=registrar_display,
        ).order_by("name")

        self.log_info(f"Found {zones.count()} active zone(s) with registrar='{registrar_display}'.")

        updated = 0
        skipped = 0
        failed = 0

        for zone in zones:
            domain = zone.name.strip().rstrip(".")
            self.log_info(f"[{zone.id}] {domain}: WHOIS lookup...")

            registry_text, registrar_text = self._run_whois_bundle(domain)
            if not registry_text and not registrar_text:
                failed += 1
                self.log_warning(f"[{zone.id}] {domain}: No WHOIS output.")
                continue

            # Prefer expiry/status from registry if available; fall back to registrar text
            base_text = registry_text or registrar_text

            expiry_date = self._parse_expiry(base_text or "")
            statuses = self._parse_status(base_text or "")

            # Registry Domain ID likely only in registry WHOIS
            registry_id = self._parse_registry_domain_id(registry_text or "")

            # Contacts typically only in registrar WHOIS; fall back to whatever we have
            contact_text = registrar_text or registry_text or ""
            admin_info   = self._parse_contact_role(contact_text, "admin")
            tech_info    = self._parse_contact_role(contact_text, "tech")
            billing_info = self._parse_contact_role(contact_text, "billing")

            changed = False

            # Expiration
            if expiry_date and zone.expiration_date != expiry_date:
                zone.expiration_date = expiry_date
                changed = True
                self.log_info(f"[{zone.id}] {domain}: expiration_date -> {expiry_date.isoformat()}")
            elif not expiry_date:
                self.log_warning(f"[{zone.id}] {domain}: Expiration not found in WHOIS.")

            # Status
            if statuses:
                status_str = ", ".join(statuses)
                current = (zone.domain_status or "").strip()
                if current != status_str:
                    zone.domain_status = status_str
                    changed = True
                    self.log_info(f"[{zone.id}] {domain}: domain_status -> {status_str}")
            else:
                self.log_warning(f"[{zone.id}] {domain}: No Domain Status found.")

            # Registry Domain ID
            if registry_id:
                if (zone.registry_domain_id or "").strip() != registry_id:
                    zone.registry_domain_id = registry_id
                    changed = True
                    self.log_info(f"[{zone.id}] {domain}: registry_domain_id -> {registry_id}")
            else:
                self.log_warning(f"[{zone.id}] {domain}: Registry Domain ID not found.")

            # Contacts (create/find then assign)
            admin_c = self._ensure_contact("admin", admin_info, commit)
            tech_c = self._ensure_contact("tech", tech_info, commit)
            billing_c = self._ensure_contact("billing", billing_info, commit)

            if admin_c and getattr(zone, "admin_c_id", None) != admin_c.pk:
                zone.admin_c = admin_c
                changed = True
                self.log_info(f"[{zone.id}] {domain}: admin_c -> {admin_c.name} (id={admin_c.pk or 'new'})")

            if tech_c and getattr(zone, "tech_c_id", None) != tech_c.pk:
                zone.tech_c = tech_c
                changed = True
                self.log_info(f"[{zone.id}] {domain}: tech_c -> {tech_c.name} (id={tech_c.pk or 'new'})")

            if billing_c and getattr(zone, "billing_c_id", None) != billing_c.pk:
                zone.billing_c = billing_c
                changed = True
                self.log_info(f"[{zone.id}] {domain}: billing_c -> {billing_c.name} (id={billing_c.pk or 'new'})")

            if not changed:
                skipped += 1
                self.log_info(f"[{zone.id}] {domain}: No changes.")
                continue

            if not commit:
                updated += 1
                self.log_success(f"[{zone.id}] {domain}: Would save changes (Commit is OFF).")
                continue

            try:
                zone.save()
                updated += 1
                self.log_success(f"[{zone.id}] {domain}: Saved changes.")
            except Exception as e:
                failed += 1
                self.log_failure(f"[{zone.id}] {domain}: Save failed: {e!r}")

        self.log_info(f"Done. Updated: {updated}, Skipped: {skipped}, Failed: {failed}.")
