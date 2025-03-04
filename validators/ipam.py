from ipam.models import Prefix
from users.models import User
from netaddr import IPNetwork,AddrFormatError

from extras.validators import CustomValidator

class prefixTenantValidator(CustomValidator):
    """ Limit users in groups in limit_groups to only adding IPs whose prefix has the associated tenant """
    def validate(self, instance, request):
        # Adding a user (including superusers) to any of these groups will enforce tenant restrictions
        # Likewise, any users not in these groups do not face restrictions. NB permissions still apply.
        limit_groups = {
            # group.name : tenant.slug
            'org_STORAGE':'org_storage',
            'org_NETWORKING':'org_networking',
            'org_SOFTWARE':'org_software',
            'org_SYSTEMS':'org_systems',
            'org_SECURTITY':'org_security'
        }
        # Groups need to be a list. Returns an empty list if there are no groups.
        user_groups = User.objects.get(username=request.user).groups.values_list('name', flat = True)
        allowed_tenant = True

        try:
            # Derive prefix
            net_address = IPNetwork(instance.address).cidr
        except AddrFormatError as e:
            self.fail('Custom Validation: Invalid address format')
        try:
            # Grab prefix tenant slug
            net_tenant = Prefix.objects.get(prefix=net_address).tenant.slug
        except Prefix.DoesNotExist:
            self.fail('Custom Validation: No existing prefix for address')
        except AttributeError:
            net_tenant = None

        for group in limit_groups:
            if group in user_groups:
                allowed_tenant = False
                tenant = limit_groups[group]
                if net_tenant == tenant:
                    allowed_tenant = True
                    # Break loop if permission found
                    break

        if allowed_tenant == False:
            self.fail(f'Custom Validation: Invalid group membership for {net_address}')
