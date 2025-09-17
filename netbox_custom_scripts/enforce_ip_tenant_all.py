# enforce_ip_tenant_all.py
from ipaddress import ip_network, ip_interface, IPv4Network
from extras.scripts import Script, ObjectVar
from ipam.models import Prefix, IPAddress

BULK_CHUNK = 1000  # safer for large updates

class EnforceIPTenantsToPrefixTenant(Script):
    """
    Enforce that every IPAddress inside the selected Prefix (or all prefixes if none selected)
    has the same tenant as the Prefix. Skips IPv4 network, broadcast, and first usable (.1).
    Uses bulk .update() to avoid pre_save signals (e.g., NetBox-DNS ipam_dnssync).
    """

    class Meta:
        name = "Enforce IP Tenants to Prefix Tenant"
        description = "Apply each prefix's tenant to all contained IPs; skip network/broadcast/gateway. Bulk DB update (no signals)."
        commit_default = True  # default UI to Commit=ON

    # Optional: if left blank, process all prefixes that have a tenant
    prefix = ObjectVar(
        model=Prefix,
        required=False,
        description="(Optional) If set, enforce only this prefix. Leave blank to process all prefixes with a tenant.",
    )

    def _first_usable_ipv4(self, net: IPv4Network):
        if net.prefixlen >= 31:
            return None
        return net.network_address + 1

    def _process_one_prefix(self, pfx: Prefix, commit: bool):
        tenant_id = Prefix.objects.filter(pk=pfx.pk).values_list("tenant_id", flat=True).first()
        if tenant_id is None:
            self.log_info(f"Skip {pfx}: no tenant set on prefix.")
            return dict(total=0, scanned=0, skipped_special=0, matched=0, mismatched=0, changed=0)

        net = ip_network(str(pfx.prefix), strict=False)
        skip = {net.network_address}
        if isinstance(net, IPv4Network):
            if net.prefixlen <= 30:
                skip.add(net.broadcast_address)
            fu = self._first_usable_ipv4(net)
            if fu:
                skip.add(fu)

        ips = IPAddress.objects.filter(
            vrf=pfx.vrf,
            address__net_contained_or_equal=str(pfx.prefix),
        )

        total = ips.count()
        self.log_info(f"[{pfx}] tenant_id={tenant_id} | commit={'ON' if commit else 'OFF'} | IPs={total}")

        ids_to_update = []
        scanned = skipped_special = matched = mismatched = 0

        for ip_obj in ips.iterator():
            host_ip = ip_interface(str(ip_obj.address)).ip
            scanned += 1
            if host_ip in skip:
                skipped_special += 1
                continue
            if host_ip not in net:
                continue
            if ip_obj.tenant_id == tenant_id:
                matched += 1
                continue
            mismatched += 1
            ids_to_update.append(ip_obj.pk)

        changed = 0
        if commit and ids_to_update:
            # Bulk update to bypass model save() and any pre_save signals (DNS plugin)
            for i in range(0, len(ids_to_update), BULK_CHUNK):
                batch = ids_to_update[i : i + BULK_CHUNK]
                IPAddress.objects.filter(pk__in=batch).update(tenant_id=tenant_id)
            changed = len(ids_to_update)

        if changed or mismatched:
            self.log_info(
                f"[{pfx}] scanned={scanned}, skipped_special={skipped_special}, "
                f"matched={matched}, mismatched={mismatched}, changes_applied={(changed if commit else 0)}"
            )

        return dict(total=total, scanned=scanned, skipped_special=skipped_special,
                    matched=matched, mismatched=mismatched, changed=changed)

    def run(self, data, commit):
        sel = data.get("prefix")

        if sel:
            prefixes = [Prefix.objects.only("id", "prefix", "vrf", "tenant").get(pk=sel.pk)]
        else:
            prefixes = list(
                Prefix.objects.filter(tenant__isnull=False)
                .only("id", "prefix", "vrf", "tenant")
                .iterator()
            )

        total_prefixes = len(prefixes)
        self.log_info(f"Commit mode: {'ON' if commit else 'OFF (dry-run)'}")
        self.log_info(f"Processing {total_prefixes} prefix(es).")

        g_total = g_scanned = g_skipped = g_matched = g_mismatched = g_changed = 0
        for pfx in prefixes:
            stats = self._process_one_prefix(pfx, commit)
            g_total += stats["total"]
            g_scanned += stats["scanned"]
            g_skipped += stats["skipped_special"]
            g_matched += stats["matched"]
            g_mismatched += stats["mismatched"]
            g_changed += stats["changed"]

        self.log_info(
            f"Overall: prefixes={total_prefixes}, ips_total={g_total}, scanned={g_scanned}, "
            f"skipped_special={g_skipped}, matched={g_matched}, mismatched={g_mismatched}, "
            f"changes_applied={(g_changed if commit else 0)}"
        )
