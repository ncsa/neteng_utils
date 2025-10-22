from extras.scripts import Script, StringVar
from django.utils import timezone
from django.core.mail import send_mail
from datetime import date
from netbox_dns.models import Zone

# Days before expiration to notify
NOTIFY_DAYS = {90, 60, 30, 15, 10, 5, 4, 3, 2, 1}


class DomainExpiryNotifier(Script):
    class Meta:
        name = "Domain Expiry Notifier (email reminders)"
        description = (
            "Emails a summary of zones whose expiration_date is exactly "
            "90/60/30/15/10/5/4/3/2/1 days away."
        )
        commit_default = False  # Commit controls whether emails are actually sent

    recipient_email = StringVar(
        description="Where to send reminders",
        default="<EMAIL>",
        required=True,
    )

    registrar_name = StringVar(
        description="Optional: filter by registrar display name (blank = all)",
        default="",
        required=False,
    )

    def run(self, data, commit):
        recipient = data["recipient_email"].strip()
        registrar = (data.get("registrar_name") or "").strip()

        today = timezone.now().date()

        qs = Zone.objects.filter(active=True, expiration_date__isnull=False)
        if registrar:
            qs = qs.filter(registrar__name__iexact=registrar)

        due = []
        for z in qs.only("id", "name", "expiration_date", "domain_status", "registrar"):
            days_left = (z.expiration_date - today).days
            if days_left in NOTIFY_DAYS and days_left >= 0:
                due.append((z, days_left))

        if not due:
            self.log_info("No domains due for notification today.")
            return

        # Sort by days_left then name for a consistent email
        due.sort(key=lambda t: (t[1], t[0].name.lower()))

        # Build email
        subject = f"[NetBox] Domain expirations due: {', '.join(sorted({d[0].name for d in due}))[:80]}"
        lines = []
        lines.append("The following domains are approaching expiration:\n")
        for zone, dleft in due:
            reg = getattr(zone.registrar, "name", "") if getattr(zone, "registrar", None) else ""
            status = (zone.domain_status or "").strip()
            lines.append(
                f"- {zone.name}  |  expires {zone.expiration_date.isoformat()}  "
                f"({dleft} day{'s' if dleft != 1 else ''} remaining)"
                + (f"  |  registrar: {reg}" if reg else "")
                + (f"  |  status: {status}" if status else "")
            )

        body = "\n".join(lines) + "\n\n— NetBox Domain Expiry Notifier"

        self.log_info(f"Preparing to notify {len(due)} domain(s) to {recipient}")

        if not commit:
            self.log_success("Commit is OFF — would send the following email:")
            self.log_info(f"To: {recipient}\nSubject: {subject}\n\n{body}")
            return

        try:
            sent = send_mail(
                subject=subject,
                message=body,
                from_email=None,          # uses DEFAULT_FROM_EMAIL
                recipient_list=[recipient],
                fail_silently=False,
            )
            if sent:
                self.log_success(f"Email sent to {recipient} covering {len(due)} domain(s).")
            else:
                self.log_failure("send_mail returned 0 (not sent). Check email backend config.")
        except Exception as e:
            self.log_failure(f"Email send failed: {e!r}")
