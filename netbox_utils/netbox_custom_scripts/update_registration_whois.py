from extras.scripts import Script, StringVar
from datetime import datetime, timezone as dt_timezone
import subprocess
import re
from importlib import import_module

WHOIS_OVERRIDES = {
    "org": "whois.pir.org",
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "us":  "whois.nic.us",
}


class NamecheapWhoisSync(Script):
    class Meta:
        name = "Sync expiration, status & Registry Domain ID via WHOIS (Namecheap)"
        description = "For zones with registrar=Namecheap, update expiration_date, domain_status, registry_domain_id from WHOIS."
        commit_default = False  # requires Commit checkbox in the UI

    registrar_name = StringVar(
        description="Registrar display name to filter on",
        default="Namecheap",
        required=True,
    )

    # ---------------- helpers: WHOIS ---------------- #

    def _tld_of(self, domain):
        d = domain.lower().strip(".")
        return d.rsplit(".", 1)[-1] if "." in d else ""

    def _run_whois_server(self, server, domain):
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

    def _run_whois_bundle(self, domain):
        """Return (registry_text, registrar_text)."""
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
            registrar_text = self._run_whois_server(None, domain)

        return registry_text, registrar_text

    # ---------------- parsers ---------------- #

    def _parse_expiry(self, text):
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
            raw = re.split(r"\s{2,}|\s\(|\s#|;", raw)[0].strip()

            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
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

    def _normalize_status(self, token):
        t = token.strip()
        t = re.sub(r"^[^\w-]+|[^\w-]+$", "", t)
        if not t:
            return None
        if re.fullmatch(r"[A-Z_]+", t):
            return t.lower()
        return t

    def _parse_status(self, text):
        out = []
        for m in re.finditer(r"^\s*Domain\s+Status:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE):
            line = m.group(1).strip()
            token = line.split()[0] if line else ""
            norm = self._normalize_status(token)
            if norm:
                out.append(norm)
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

    def _parse_registry_domain_id(self, text):
        m = re.search(r"^\s*Registry\s+Domain\s+ID\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
        m = re.search(r"^\s*Domain\s+ID\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else None

    # -------- coerce domain_status to a valid DB value -------- #

    def _coerce_domain_status(self, ZoneModel, statuses):
        """
        Return a value acceptable for Zone.domain_status:
        - If the field has CHOICES: pick the first WHOIS status present in choices.
        - Else: join all statuses with ", " (legacy free-form).
        """
        if not statuses:
            return None, []

        try:
            field = ZoneModel._meta.get_field("domain_status")
            choices = getattr(field, "choices", None)
        except Exception:
            choices = None

        extras = []
        if choices:
            allowed = {c[0] for c in choices}
            chosen = None
            for s in statuses:
                if s in allowed:
                    chosen = s
                    break
            if not chosen:
                # fall back: try exact text before normalization (rare)
                chosen = statuses[0]
            # anything beyond the chosen is "extra" (for logging only)
            extras = [s for s in statuses if s != chosen]
            return chosen, extras

        # No choices defined -> free-form: store all
        return ", ".join(statuses), []

    # ---------------- main ---------------- #

    def run(self, data, commit):
        # Lazy-load model so the module imports cleanly
        try:
            Zone = getattr(import_module("netbox_dns.models"), "Zone")
        except Exception as e:
            self.log_failure(f"Could not import netbox_dns Zone model: {e!r}. Is the plugin installed and migrated?")
            return

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

            base_text = registry_text or registrar_text

            expiry_date = self._parse_expiry(base_text or "")
            statuses = self._parse_status(base_text or "")
            registry_id = self._parse_registry_domain_id(registry_text or "")

            changed = False

            # Expiration
            if expiry_date and zone.expiration_date != expiry_date:
                zone.expiration_date = expiry_date
                changed = True
                self.log_info(f"[{zone.id}] {domain}: expiration_date -> {expiry_date.isoformat()}")
            elif not expiry_date:
                self.log_warning(f"[{zone.id}] {domain}: Expiration not found in WHOIS.")

            # Domain status (respect choices)
            if statuses:
                status_value, extras = self._coerce_domain_status(Zone, statuses)
                if status_value:
                    current = (zone.domain_status or "").strip()
                    if current != status_value:
                        zone.domain_status = status_value
                        changed = True
                        self.log_info(f"[{zone.id}] {domain}: domain_status -> {status_value}")
                    if extras:
                        self.log_info(f"[{zone.id}] {domain}: additional statuses (ignored for DB): {', '.join(extras)}")
            else:
                self.log_warning(f"[{zone.id}] {domain}: No Domain Status found.")

            # Registry Domain ID (from registry WHOIS)
            if registry_id:
                if (zone.registry_domain_id or "").strip() != registry_id:
                    zone.registry_domain_id = registry_id
                    changed = True
                    self.log_info(f"[{zone.id}] {domain}: registry_domain_id -> {registry_id}")
            else:
                self.log_warning(f"[{zone.id}] {domain}: Registry Domain ID not found.")

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
