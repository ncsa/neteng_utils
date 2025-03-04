Import DHCP Lease information into Netbox:

General: 

These are a collection of scripts that are used to generate ISC KEA DHCP configurations from Netbox.


Our Setup:   With migrating from our existing IPAM to Netbox we wanted a way to store DHCP lease information inside Netbox.   A custom field is created inside Netbox under the IPAM| IP address called "mac_address" that is then used to store the MAC address and tie it to the IP address.   An Event Rule is then created (inside Netbox) with an Object Type of IPAM>IP Address, and an Event Type of "Object updated".   The KEA DHCP server is also running a webhook running (listener) that waits for a webhook call to be issued from the Netbox server.  When the webhook is called a script (pull_dhcp_from_netbox.py) is executed on the KEA DHCP server which pulls the information from Netbox and generates includes file for KEA DHCP.



### update_netbox_with_dhcpd.py ###

This script will parse an existing ISC dhcpd.conf (make sure the file is located in the same directory as script)  file pull out the fixed-address and hardware ethernet (MAC) address and populate the Netbox IPAM->IP Address with the MAC address.  This assumes a Netbox custom field was created prior to running the script.


Custom Field information:

A Custom field object is created under IPAM| IP Address. 

Regular expression set  to 

```
^(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}$
```



### pull_dhcp_from_netbox.py ###
This script can be run (in our case the KEA DHCP server) which runs and pulls IP/MAC information from Netbox and generates files that are included in the KEA dhcp configuration and restarts the KEA DHCP server.


Assumptions:

- You have previously defined the your subnets inside your kea-dhcp4.conf and kea-dhcp6.conf files.
- The file that is generated is based on prefix inside of Netbox.   In this case 172.24.2.64/26 produces kea-dhcp-v4-172.24.2.64_26-reserverations.json
- The output from Netbox will be in JSON format, so simply including that output will work.



Example:


         "subnet4": [
            {
                "subnet": "172.24.2.64/26",
                "option-data": [
                    {
                        "name": "routers",
                        "data": "172.24.2.65"
                    },
                    {
                        "name": "domain-name-servers",
                        "data": "1.1.1.1, 2.2.2.2"
                    }
                  ],
                "pools": [
                            { "pool": "172.24.2.66 - 172.24.2.126" }
                ],
                "reservations":
                 <?include "/etc/kea/host_reservations/kea-dhcp-v4-172.24.2.64_26-reservations.json"?>
                  }
                ]