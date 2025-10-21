# /opt/netbox/netbox/validators/ipam.py

import logging
import os

from django.core.exceptions import ValidationError
from extras.validators import CustomValidator
from ipam.models import Prefix
from users.models import User
from netaddr import IPNetwork, IPSet, IPAddress, AddrFormatError
from netbox_dns.models import Zone

logger = logging.getLogger(__name__)


def load_group_mapping():
    """Load group-to-tenant mappings from group_mapping.txt"""
    mapping = {}
    base_dir = os.path.dirname(__file__)
    file_path = os.path.join(base_dir, "group_mapping.txt")
    try:
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    group, tenant = [x.strip() for x in line.split(":", 1)]
                    mapping[group] = tenant
                except ValueError:
                    logger.warning("Skipping malformed line in %s: %r", file_path, line)
    except FileNotFoundError:
        logger.error("group_mapping.txt not found in %s", base_dir)
    return mapping


class prefixTenantValidator(CustomValidator):
    """Limit users in groups in group_mapping.txt to only adding IPs whose prefix has the associated tenant"""

    def validate(self, instance, request):
        # ---- superuser bypass ----
        user = getattr(request, "user", None)
        if getattr(user, "is_superuser", False):
            return
        # --------------------------

        limit_groups = load_group_mapping()

        # Groups need to be a list. Returns an empty list if there are no groups.
        user_groups = User.objects.get(username=request.user).groups.values_list("name", flat=True)
        allowed_tenant = True

        try:
            # Derive prefix
            net_address = IPNetwork(instance.address).cidr
        except AddrFormatError:
            self.fail("Custom Validation: Invalid address format")

        try:
            # Grab prefix tenant slug
            net_tenant = Prefix.objects.get(prefix=net_address).tenant.slug
        except Prefix.DoesNotExist:
            self.fail("Custom Validation: No existing prefix for address")
        except AttributeError:
            net_tenant = None

        for group in limit_groups:
            if group in user_groups:
                allowed_tenant = False
                tenant = limit_groups[group]
                if net_tenant == tenant:
                    allowed_tenant = True
                    break

        if allowed_tenant is False:
            self.fail(f"Custom Validation: Invalid group membership for {net_address}")


class PrivateIPMustUseInternalZone(CustomValidator):
    """
    If an IP is in any CIDR in BLOCKED and dns_name is set,
    the dns_name must end in a Zone tagged with INTERNAL_ZONE_TAGS.

    EXCEPTION (bypass): users in BYPASS_GROUPS (e.g., org_ADMIN) are NOT restricted
    by the internal-zone requirement for private/RFC1918 space. Superusers are also bypassed.
    """

    # Private ranges to enforce against
    BLOCKED = IPSet([
        "10.0.0.0/8",
        "172.16.0.0/12",
        # Add "192.168.0.0/16" here too if you want it enforced.
        # "192.168.0.0/16",
    ])

    INTERNAL_ZONE_TAGS = {"zone_internal"}

    # Groups that bypass the internal-zone requirement on private space
    BYPASS_GROUPS = {"org_ADMIN"}

    def _user_in_bypass(self, request) -> bool:
        """Return True if the acting user should bypass the internal-zone check."""
        try:
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return False
            # Superusers bypass everything
            if getattr(user, "is_superuser", False):
                return True
            # Members of any group in BYPASS_GROUPS bypass the internal-zone rule
            return user.groups.filter(name__in=self.BYPASS_GROUPS).exists()
        except Exception:
            # Be conservative if anything goes sideways
            return False

    def validate(self, instance, request):
        dns_name = (getattr(instance, "dns_name", "") or "").strip().rstrip(".")
        if not dns_name:
            return  # no name; nothing to enforce

        # Parse IP from instance.address (e.g., "172.27.0.2/21")
        try:
            ip = IPNetwork(instance.address).ip
        except Exception as e:
            logger.warning(
                "PrivateIPMustUseInternalZone: could not parse address=%r (%s)",
                getattr(instance, "address", None), e
            )
            return  # be permissive if unparsable

        # Only enforce for private/blocked ranges
        if ip not in self.BLOCKED:
            return

        # Bypass for superusers and BYPASS_GROUPS (e.g., org_ADMIN)
        if self._user_in_bypass(request):
            return

        # Find best-matching Zone by suffix of dns_name
        zone = self._match_zone_by_suffix(dns_name)
        if not zone:
            raise ValidationError({
                "dns_name": (
                    f"‘{dns_name}’ is not under any known DNS Zone; private address {ip} "
                    f"must use a Zone tagged {sorted(self.INTERNAL_ZONE_TAGS)}."
                )
            })

        # Require internal tag on the matched zone
        if not zone.tags.filter(slug__in=self.INTERNAL_ZONE_TAGS).exists():
            raise ValidationError({
                "dns_name": (
                    f"Private address {ip} must use an Internal Zone (zone_internal)."
                )
            })
        # OK if we get here

    # ---------- helpers ----------
    def _match_zone_by_suffix(self, fqdn: str):
        name = (fqdn or "").lower().rstrip(".")
        if not name:
            return None
        labels = name.split(".")
        # longest -> shortest suffix list
        suffixes = [".".join(labels[i:]) for i in range(len(labels))]
        candidates = Zone.objects.filter(name__in=suffixes).only("id", "name")
        best = None
        best_len = -1
        for z in candidates:
            zname = (z.name or "").lower()
            if name.endswith(zname) and len(zname) > best_len:
                best = z
                best_len = len(zname)
        return best
