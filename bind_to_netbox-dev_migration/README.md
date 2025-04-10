# neteng-netbox-tools


There are several scripts in this directory providing various tasks.  Most of these scripts are for the use with Netbox and the netbox-dns-plugin (https://github.com/peteeckel/netbox-plugin-dns) and migrating from our older IPAM/DNS system.

#### Notes
All scripts need to have various API Tokens and URLs updated before running the script.


### zone_transfer.sh ###
Basic script that will perform Zone transfers (axfr) from your existing DNS server to your local machine.   Simply add the zone name to zones.txt and run the script (./zone_transfer.sh).   Make sure your machine (or what ever machine you are running the script from can perform zone transfers.


### update_managed_records.py ###
This script will read the zones files that were created from the zone_transfer.sh script, and creates the IP address (with correct CIDR information) for all A and AAAA records found in the zone files, and updates the DNS Name field for that IP address.   This will in turn update the Managed Records section of the netbox-dns-plugins.

Caveats:   
	- It assumes that you have already created the relevant prefixes in IPAM->Prefixes.   If not it will throw an error.  This is by design as in our case, not all DNS entries we host are on our IP space.  
	- If you have run this script previously it will add new records but will not delete records that are no longer present in the zone files.
	- If you have multiple A records that point to the same IP address, it will over write the DNS Name field with the most recently processed information.  

	
	 Example:  
		- foo.bar.com 	1.2.3.4  
		- foo1.bar.com	1.2.3.4   
		- foo2.bar.com	1.2.3.4  
	
The end result for 1.2.3.4 DNS Name will be foo2.bar.com   If you need those addition records, they need to be manually placed in the "Records" section of the netbox-dns-plugin section.



### update unmanaged_records.py ###
This script will parse your zone files created by the "zone_transfer.sh" script and will generate MX, SRV, CNAME, and TXT records in the DNS>Records section of the netbox-dns-plugin. 

Note:  NS recourds are not implemented due to how Netbox assicates nameservers with previously defined NS servers in the plugin.   It is possible to do, but not worth the effort at this time.


