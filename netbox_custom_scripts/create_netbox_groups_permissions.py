#!/usr/bin/env python3
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from extras.scripts import Script, StringVar
from tenancy.models import Tenant
from users.models import ObjectPermission, Group  # <-- use NetBox's Group

class CreateOrgGroupAndPerms(Script):
    """
    Create (or update) a Tenant named like the provided org (e.g., org_IRST),
    create (or update) a Group with the same name,
    and attach three ObjectPermissions scoped by {"tenant__name": "<ORG>"}:

      1) "<ORG> - Manage IP Addresses"
         - object_types: ipam.ipaddress
         - actions: view, add, change, delete

      2) "<ORG> - Update Unmanaged Records"
         - object_types: netbox_dns.record
         - actions: view, change

      3) "<ORG> - View Prefixes and Vlans"
         - object_types: ipam.prefix, ipam.vlan
         - actions: view
    """

    org_name = StringVar(
        description='Organization key (e.g., "org_IRST"). Used for Tenant & Group name and in constraints.',
        required=True,
    )
    group_description = StringVar(
        description='Optional description (applied to the Tenant only).',
        required=False,
    )

    class Meta:
        name = "Create Tenant + Group + Scoped Permissions for Org"
        description = "Creates/updates a Tenant and Group and 3 ObjectPermissions scoped to tenant__name=<ORG>."
        commit_default = True

    # ----------------- helpers -----------------

    def _get_ct(self, app_label: str, model: str) -> ContentType:
        return ContentType.objects.get(app_label=app_label, model=model)

    def _ensure_tenant(self, name: str, description: str | None, commit: bool) -> Tenant:
        try:
            tenant = Tenant.objects.get(name=name)
            changed = False

            if description is not None and (tenant.description or "") != description:
                changed = True
                if commit:
                    tenant.description = description

            if changed and commit:
                tenant.save()
                self.log_success(f'Updated tenant "{name}".')
            elif changed and not commit:
                self.log_info(f'[DRY-RUN] Would update tenant "{name}".')
            else:
                self.log_info(f'Tenant "{name}" already exists; no changes needed.')

            return tenant

        except Tenant.DoesNotExist:
            if commit:
                tenant = Tenant.objects.create(
                    name=name,
                    slug=slugify(name),
                    description=description or "",
                )
                self.log_success(f'Created tenant "{name}".')
                return tenant
            else:
                self.log_info(f'[DRY-RUN] Would create tenant "{name}" with slug "{slugify(name)}".')
                return Tenant(name=name, slug=slugify(name), description=description or "")

    def _ensure_group(self, name: str, commit: bool) -> Group:
        try:
            grp = Group.objects.get(name=name)
            self.log_info(f'Group "{name}" already exists; no changes needed.')
            return grp
        except Group.DoesNotExist:
            if commit:
                grp = Group.objects.create(name=name)
                self.log_success(f'Created group "{name}".')
                return grp
            else:
                self.log_info(f'[DRY-RUN] Would create group "{name}".')
                return Group(name=name)

    def _ensure_permission(
        self,
        name: str,
        description: str,
        actions: list[str],
        content_types: list[ContentType],
        constraints: dict,
        attach_group: Group,
        commit: bool,
    ) -> ObjectPermission:
        desired_ct_ids = [ct.pk for ct in content_types]

        try:
            perm = ObjectPermission.objects.get(name=name)
            changed = False

            if not perm.enabled:
                changed = True
                if commit:
                    perm.enabled = True

            if perm.description != description:
                changed = True
                if commit:
                    perm.description = description

            if sorted(perm.actions or []) != sorted(actions):
                changed = True
                if commit:
                    perm.actions = actions

            if (perm.constraints or {}) != (constraints or {}):
                changed = True
                if commit:
                    perm.constraints = constraints

            if changed and commit:
                perm.save()
                self.log_success(f'Updated permission "{name}" fields.')
            elif changed and not commit:
                self.log_info(f'[DRY-RUN] Would update permission "{name}" fields.')
            else:
                self.log_info(f'Permission "{name}" exists; fields OK.')

            # Sync object_types (by IDs)
            current_ct_ids = set(perm.object_types.values_list("id", flat=True))
            desired_ct_ids_set = set(desired_ct_ids)
            if current_ct_ids != desired_ct_ids_set:
                if commit:
                    perm.object_types.set(desired_ct_ids)
                    self.log_success(f'Updated object_types for "{name}".')
                else:
                    self.log_info(f'[DRY-RUN] Would set object_types for "{name}" to IDs {desired_ct_ids}.')

            # Ensure group attached (only if saved)
            if attach_group.pk:
                if not perm.groups.filter(pk=attach_group.pk).exists():
                    if commit:
                        perm.groups.add(attach_group)
                        self.log_success(f'Attached group "{attach_group.name}" to permission "{name}".')
                    else:
                        self.log_info(f'[DRY-RUN] Would attach group "{attach_group.name}" to permission "{name}".')
            else:
                self.log_info(f'[DRY-RUN] Would attach group "{attach_group.name}" to permission "{name}".')

            return perm

        except ObjectPermission.DoesNotExist:
            if commit:
                perm = ObjectPermission.objects.create(
                    name=name,
                    description=description,
                    enabled=True,
                    actions=actions,
                    constraints=constraints or {},
                )
                perm.object_types.set(desired_ct_ids)
                self.log_success(f'Created permission "{name}".')

                if attach_group.pk:
                    perm.groups.add(attach_group)
                    self.log_success(f'Attached group "{attach_group.name}" to permission "{name}".')
                else:
                    self.log_info(f'[DRY-RUN] (new group without PK) Would attach group "{attach_group.name}" to "{name}".')
                return perm
            else:
                self.log_info(f'[DRY-RUN] Would create permission "{name}".')
                self.log_info(f'[DRY-RUN] Would set object_types IDs: {desired_ct_ids}')
                self.log_info(f'[DRY-RUN] Would set actions: {actions}')
                self.log_info(f'[DRY-RUN] Would set constraints: {constraints}')
                self.log_info(f'[DRY-RUN] Would attach group "{attach_group.name}" to permission "{name}".')
                return ObjectPermission(name=name)

    # ----------------- main -----------------

    def run(self, data, commit):
        org = data["org_name"].strip()
        desc = (data.get("group_description") or "").strip()

        if not org:
            self.log_failure("org_name cannot be empty.")
            return

        self.log_info(f'Preparing Tenant, Group & Permissions for org: "{org}" (commit={commit})')

        # 0) Ensure Tenant (description applied here)
        tenant = self._ensure_tenant(name=org, description=desc, commit=commit)

        # 1) Ensure Group (NetBox users.models.Group)
        group = self._ensure_group(name=org, commit=commit)

        # Resolve content types
        ct_ipaddress = self._get_ct("ipam", "ipaddress")
        ct_prefix = self._get_ct("ipam", "prefix")
        ct_vlan = self._get_ct("ipam", "vlan")
        ct_dns_record = self._get_ct("netbox_dns", "record")

        constraints = {"tenant__name": org}

        # 1) Manage IP Addresses
        perm1_name = f"{org} - Manage IP Addresses"
        self._ensure_permission(
            name=perm1_name,
            description=perm1_name,
            actions=["view", "add", "change", "delete"],
            content_types=[ct_ipaddress],
            constraints=constraints,
            attach_group=group,
            commit=commit,
        )

        # 2) Update Unmanaged Records
        perm2_name = f"{org} - Update Unmanaged Records"
        self._ensure_permission(
            name=perm2_name,
            description=perm2_name,
            actions=["view", "change"],
            content_types=[ct_dns_record],
            constraints=constraints,
            attach_group=group,
            commit=commit,
        )

        # 3) View Prefixes and Vlans
        perm3_name = f"{org} - View Prefixes and Vlans"
        self._ensure_permission(
            name=perm3_name,
            description=perm3_name,
            actions=["view"],
            content_types=[ct_prefix, ct_vlan],
            constraints=constraints,
            attach_group=group,
            commit=commit,
        )

        self.log_success("Completed. Re-run with Commit enabled to persist changes." if not commit else "All done.")
