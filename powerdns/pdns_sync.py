from collections import defaultdict
from copy import copy
import powerdns

from extras.scripts import *
from django.db.models import Q  # Required for querying NetBox models

try:
    from netbox_dns.models import Zone, Record
    HAVE_DNS_PLUGIN = True
except ImportError:
    HAVE_DNS_PLUGIN = False

SUPPORTED_TYPES = ['A', 'AAAA', 'PTR', 'CNAME', 'SOA', 'TXT', 'DS', 'DNAME', 'MX', 'SRV']
MANAGED_COMMENT = 'rrset managed by netbox'
MANAGED_ACCOUNT = 'netbox_sync'
PDNS_API_ENDPOINT = 'http://<pdns-url>:8081/api/v1'
PDNS_API_KEY = 'API KEY'

name = "DNS related scripts"

class PdnsZoneSync(Script):
    class Meta:
        name = "Sync all zones to PowerDNS API"

    def run(self, data, commit):
        if not HAVE_DNS_PLUGIN:
            raise AbortScript('You need netbox_plugin_dns installed to run this script.')

        response = ''

        # Automatically fetch ALL zones
        zones = Zone.objects.all()
        if not zones:
            return "No zones found in NetBox."

        for zone in zones:
            response += self.sync_zone(zone, commit)

        return response

    def sync_zone(self, zone: Zone, commit):
        api_client = powerdns.PDNSApiClient(
            api_endpoint=PDNS_API_ENDPOINT.rstrip('/'),
            api_key=PDNS_API_KEY,
        )
        server = powerdns.PDNSEndpoint(api_client).servers[0]
        pdns_zone = server.get_zone(zone.name + '.')

        if not pdns_zone:
            self.log_warning(f'Zone {zone.name} not found on PowerDNS server. Creating it.')
            pdns_zone = server.create_zone(zone.name + '.', kind='Master', nameservers=[])

            # Ensure SOA record is created and properly managed
            soa_record = powerdns.RRSet(
                name=zone.name + '.',
                rtype='SOA',
                records=[{
                    'content': f'ns1.{zone.name}. hostmaster.{zone.name}. 1 10800 3600 604800 3600',
                    'disabled': False
                }],
                ttl=3600,
                changetype='UPSERT',
                comments=[{'content': MANAGED_COMMENT, 'account': MANAGED_ACCOUNT}]
            )
            pdns_zone.create_records([soa_record])

            if not pdns_zone:
                return f'Failed to create zone {zone.name}. Skipping.\n'

        to_add, to_update, to_delete = {}, [], []
        managed_comment = [{'content': MANAGED_COMMENT, 'account': MANAGED_ACCOUNT}] if MANAGED_COMMENT and MANAGED_ACCOUNT else []

        pdns_managed_rrsets = dict()
        pdns_unmanaged_rrsets = set()
        for record in pdns_zone.records:
            if record['type'] not in SUPPORTED_TYPES:
                continue
            if not self.is_rrset_managed(record):  # Fix: Ensure this method exists
                self.log_debug(f'PDNS Record not managed by netbox: {record}')
                pdns_unmanaged_rrsets.add((record['name'], record['type']))
                continue
            pdns_managed_rrsets[(record['name'], record['type'])] = record
            self.log_debug(f'Added managed PDNS record: {record}')

        netbox_records = defaultdict(lambda: {'pdns_rrset': None, 'records': []})
        for record in zone.records.filter(type__in=SUPPORTED_TYPES):
            self.log_debug(f'Processing netbox record: {record}')
            name_type = (record.fqdn, record.type)
            if name_type in pdns_unmanaged_rrsets:
                self.log_warning(f'Found existing unmanaged rrset for {record}. Not syncing.')
            elif name_type in pdns_managed_rrsets:
                rrset = pdns_managed_rrsets.pop(name_type)
                netbox_records[name_type]['pdns_rrset'] = rrset
                netbox_records[name_type]['records'].append(record)
            elif name_type in netbox_records:
                netbox_records[name_type]['records'].append(record)
            else:
                self.log_info('No existing rrset found. Will be created')
                rrset = to_add.get(name_type)
                if rrset:
                    rrset['records'].append({'content': str(record.value), 'disabled': False})
                else:
                    rrset = powerdns.RRSet(
                        name=record.fqdn,
                        rtype=record.type,
                        records=[{'content': str(record.value), 'disabled': False}],
                        ttl=record.ttl or zone.default_ttl,
                        changetype='REPLACE',
                        comments=managed_comment,
                    )
                    to_add[name_type] = rrset

        for name_type in pdns_managed_rrsets:
            self.log_info(f'Found managed rrset without netbox record {name_type} Will be deleted')
            rrset = powerdns.RRSet(
                name=name_type[0],
                rtype=name_type[1],
                records=[],
            )
            to_delete.append(rrset)

        if commit:
            if to_delete:
                self.log_debug('Calling pdns api to delete')
                pdns_zone.delete_records(to_delete)
                self.log_debug(f'call success')
            if to_add or to_update:
                self.log_debug('Calling pdns api to create/update')
                pdns_zone.create_records(list(to_add.values()) + to_update)
                self.log_debug(f'call success')

        return f'Zone {zone.name} synced successfully.\n'

    def is_rrset_managed(self, rrset) -> bool:
        """ Added Back: Checks if RRSet is managed by NetBox """
        if MANAGED_COMMENT and MANAGED_ACCOUNT:
            for comment in rrset.get("comments", []):
                if comment.get('content') == MANAGED_COMMENT and comment.get('account') == MANAGED_ACCOUNT:
                    return True
        return False
