Gather Ubuntu Server Version and import into Netbox

This is a basic ansible playbook that connects to Ubuntu based systems (though it can be extended to other OS platforms as well) gathers the release of the running os (example 20.04, 22.04, 24.04, etc) and populates a custom field inside of Netbox. 


Custom Field:

Object Types: DCIM->Device and Virtualization > Virtual Machine
Name: os_version
Label OS Version

