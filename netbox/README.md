This is a general collection of scripts (python and ansible playbooks) used for the migration to Netbox (specifically for DNS and KEA DHCP) from other platforms (such as ISC BIND and ISC DHCP).  

Layout is as follows:

**ansible_netbox:**
  - General collection of tools and quality of life scripts for using Netbox as a source of truth on the network.
    
**bind_to_netbox_migration:**
  - Tools for taking existing BIND zone files and using them to update Netbox, and more specifically netbox-dns-plugin zones.
    
**dhcp_netbox:**
  - Scripts for taking an existing ISC dhcpd.conf and updating IP addresses in Netbox with the mac address from the config file
  - Also explains the work from of using Netbox to populate KEA DHCP configuration file with lease information

**domain_registration**
  - Scripts that parse whois information and updates Netbox-DNS Registration information (such as expiration date, domain status etc
  - Script that will send you period emails for when domains are set to expire.

**Validators:**
  - Inforces permissions on IP addresses based on tenant information
