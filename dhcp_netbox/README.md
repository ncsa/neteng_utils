Import DHCP Lease information into Netbox:

General: 

These are a collection of scripts that are used to generate ISC KEA DHCP configurations from Netbox.


### update_netbox_with_dhcpd.py ###

This script will parse an existing ISC dhcpd.conf (make sure the file is located in the same directory as script)  file pull out the fixed-address and hardware ethernet (MAC) address and populate the Netbox IPAM->IP Address with the MAC address.  This assumes a Netbox custom field was created prior to running the script.


Custom Field information:

A Custom field object is created under IPAM| IP Address. 

Regular expression set  to 

```
^(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}$
```


