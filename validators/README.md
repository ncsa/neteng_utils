## Netbox Tenant Validator

#### Credit to:  Reddit.com/u/pythbit



### Overview:

#### Due to limitations of Netbox, IP Addresses do not directly inherit permissions from the prefixes that they are part of.   This causes issues when trying to setup permissions to restrict users from only modifying prefixes that they own.  This tenant validator will check to make sure correct permissions exist on the prefix before allowing the user to allocate an IP address inside of that prefix.


#### How to use:

#### Place the ipam.py script inside the directory "/opt/netbox/netbox/validators" .   Then add this line to your /opt/netbox/netbox/netbox/confiuration.py file:

```
############ Custom Validators ##############
CUSTOM_VALIDATORS = { 'ipam.ipaddress': ( 'validators.ipam.prefixTenantValidator', ) }
```

#### Restart Netbox 
    #systemctl restart netbox netbox-rq




The validator uses tenants which users can be assigned to those tenants:

        limit_groups = {
            # group.name : tenant.slug
            'org_SET':'org_set',
            'org_SHIPREC':'org_shiprec',
            'org_SOFTWARE':'org_software',
            'org_ASD':'org_asd',
            'org_IRST':'org_irst'
        }


Make sure the user is added to those particular tenants.


Permissions:


