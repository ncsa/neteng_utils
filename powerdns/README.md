There are several scripts in this directory providing various tasks.  Most of these scripts are for the use with Netbox and the netbox-dns-plugin (https://github.com/peteeckel/netbox-plugin-dns) and using PowerDNS as your authorative DNS Server.

#### Notes
All scripts need to have various API Tokens and URLs updated before running the script.


### nuke_records.py ###
This was created for testing (and screwing up) with syncing between Netbox and PowerDNS.   This will connect to your PowerDNS API and delete the records that have been sync'ed over.


### pdns_sync.py ###
This is  Netbox custom script which can be placed in your custom scripts directory.  The script will pull all zones out of Netbox (netbox-dns-plugin) create the zone in PowerDNS, and sync all records from that zone into PowerDNS via the PowerDNS API.  

- Note, PowerDNS api can be much more stringent in enforcing RFC compliance than Netbox.  You may have to massage the data if you run into errors.
