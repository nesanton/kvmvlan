# kvmvlan
vlan management for KVM based on libvirt hooks


Linux bridge can do proper VLAN management, but for some reason people seem to stick to the old way of creating interface.vlan_id  (eth0.100) subinterfaces and making yet another bridge on top of each.

Netlink command `bridge vlan add <dev> vid <id> [pvid] [untagged]` for example can add a vlan tag on any interface participating in a bridge. `bridge vlan show` would show all the tags on all interfaces. There's whole lot of other arguments which make Linux bridges act just like normal managed switches. So why not?

Too bad Centos cannot make these settings persistent across reboots. At least in a nice clean config-files way.
ifup.local can help of course, but not always.

KVM, for instance, with its tap interfaces cannot trully benefit from ifup.local. Tap interfaces are either preset and fed to libvirt with <dev> tags or created at qemu guest start time. The former cannot be persistently configured in Centos (this can go to ifup.local), the latter are somewhat ethemeral. In both cases, assigning vlans tags to them, especially dynamically, should be done after qemy guest start.

The idea of this little project is to have vlan management handled by libvirt itself. Or rather by [libvirt hooks](https://www.libvirt.org/hooks.html)


## Libvirt hooks

Please consult [Official guide](https://www.libvirt.org/hooks.html) for full documentation

We will be using only two hooks:
* /etc/libvirt/hooks/daemon
* /etc/libvirt/hooks/qemu

The first one is triggered every time when the Libvirt daemon is started, stopped or reloaded (SIGHUP also counts).
The other is executed on qemu guest's events:
* Before a QEMU guest is started (prepare begin -)
* After libvirt has finished labeling all resources, but has not yet started the guest (start begin -)
* After the QEMU process has successfully started up (started begin -)
* When a QEMU guest is stopped, before libvirt restores any labels (stopped end -)
* When a QEMU guest is stopped, after libvirt has released all resources (release end -)
* At the beginning of incoming migration (migrate begin -)

## Vlan management for KVM

Let's refer to the following diagram as an example setup. The shape in the middle is our virtual switch or vswitch. Rectangles attached to it are ports of this switch. 
eth0 is the only physical port. It can also be a bonding of several links

There are two VMs. First one will have one adapter which it is likely to detect as eth0 inside its OS. 
The other will have two interfaces supposedly detected as eth0 and eth1 inside its OS.

Names vethX were made up to refer to switch ports allocated to VMs from the host. It is important to understand the difference between vethX on the host and ethX inside a VM. These names are actually coming from VMs' xml configs.

br0 serves two functions: on one hand it is a name for the switch. On the other hand it is a port thru which the host itself is connected to outside. Host's ip settings can be set here and, yes, it can also have its PVID and extra VIDs

All the physical (eth0) and virtual (vethX) ports are attached to the bridge br0 on the configuration files level. Former thru /etc/sysconfig/network-scripts/ files and latter thru VM's XML configuration found in /etc/libvirt/qemu/. So there is no need in specifying the bridge name in commands "bridge vlan add ...". 

```
                                ____________________
                          _____|                    |         
                         |     | pvid=1             |
 Physical switch --------|eth0 | vids = 100,200,300 |
                         |_____|                    |_____
                               |       pvid=100     |veth0|------VM1(eth0)
                               |       vids=200,300 |_____|    
                               |                    |_____
                               |       pvid=1       |veth1|------VM2(eth0)
                               |       vids=100,200 |_____|    
                               |                    |_____
                               |       pvid=300     |veth2|------VM2(eth1)
                               |       vids= N/A    |_____|    
                               |                    | 
                               |                    |
                               |   pvid=1           | 
                               |___vids=200_________|
                                     | br0  |
                                     |______|
                                         |
                                         |------ to KVM host
```

## Sample network configuration

Physical port:

```bash
$ cat /etc/sysconfig/network-scripts/ifcfg-eth0
NAME="eth0"
HWADDR="xx:xx:xx:xx:xx:xx" 
ONBOOT="yes"
BOOTPROTO="none"
DEVICE="eth0"
BRIDGE="br0"
```

Bridge:

```bash 
$ cat /etc/sysconfig/network-scripts/ifcfg-br0
DEVICE="br0"
TYPE="Bridge"
ONBOOT="yes"
IPADDR="host.ip.address.here"
NETMASK="the.sub.net.mast"
NETWORK="first.ip.of.subnet"
BROADCAST="last.ip.of.subnet"
BOOTPROTO="static"
```

## Hooks Design

The vlan management is not the only thing which can be done with libvirt hooks. If we put all the vlan management logic in the two scripts /etc/libvirt/daemon and /etc/libvirt/qemu it will be very hard to implement other functions without touching existing code. Besides it'll look terrible.

There is a good approach for any kind of hooks when the hook scripts do not carry any functional logic at all.Instead they just run all scripts found in a predefined locations.
That'd allow to add more functions just by adding scripts to these folders.

Our schema is as follows:
 
```
 /etc/libvirt/hooks - parent directory for all hooks logic - hence fully portable
 /etc/libvirt/hooks/conf - all needed config files go here
 /etc/libvirt/hooks/logs - here go all the logs 
 /etc/libvirt/hooks/daemon - hook for libvirt daemon events
 /etc/libvirt/hooks/qemu - hook for qemu guests events
 /etc/libvirt/hooks/daemon_<phase>.d  - folders with scripts triggered by daemon hook at <phase>
 /etc/libvirt/hooks/qemu_<phase>.d - folders with scripts triggered by qemu hook at <phase>
```

since both daemon and qemu hooks receive parameters from libvirt when they are triggered it would make sense to organize our scripts using these parameters.

To keep it simple, we'd say that everything we are interested in for the daemon hook would be:

* start
* shutdown
* reaload

So there are three folders to put our scripts to:

* /etc/libvirt/hooks/daemon_start.d - anything to run when (after) libvirt starts
* /etc/libvirt/hooks/daemon_shutdown.d - when libvirt stops (before)
* /etc/libvirt/hooks/daemon_reload.d - when libvirt receives SIGHUP or explicitly reloaded with "systemctl reload libvirt". Symbolic links from _start.d might be handy

Pretty much everything libvirt could do about gemu hook is supported on folder level as well. For example:

hook:
```
/etc/libvirt/hooks/qemu guest_name prepare begin -
```
Folder for scripts to run:
```
/etc/libvirt/hooks/qemu_prepare_begin.d
```

Simply create the directory if it's not there and stuff it with scripts
More on hooks [here](https://www.libvirt.org/hooks.html)


Every script can have a config file and a log file:

script:
```
/etc/libvirt/hooks/qemu_started_begin.d/something_to_do_on_VM_start.sh
```
config:
```
/etc/libvirt/hooks/conf/something_to_do_on_VM_start.conf
``` 
log:
```
/etc/libvirt/hooks/logs/qemu_started_begin_<VM_name>_something_to_do_on_VM_start.log
```

Log file is rewritten every time the script is run.
It is possible to link a script to another folder if needed:

```
ln -s /etc/libvirt/hooks/'''qemu_started_begin.d'''/something_to_do_on_VM_start.sh /etc/libvirt/hooks/'''qemu_stopped_end.d'''/something_to_do_on_VM_start.sh
```

So it'll be triggered during another event too. It'll use the same config, but will report to a different log file, because logs reflect events in filenames.


## Finally to vlan management

Like a normal switch, virtual one has trunk and access ports. Trunk is an interconnect between switches, access is where some client device is connected (VM). In our example the trunk port is bond0, access ports are vethX. br0 is an access port as well but it is somewhat special. 

- bond0 and br0 are persistent due to configuration from previous chapter and, more importantly, they are "global" to all the VMs 
- vethX are on the contrary ephemeral and their VLAN membership is local, it does not affect other VMs or ports.

Let's put foundation of our vlan management into two config files:

* **bridge_init.conf** for global bridge settings (persistent)
* **vlan_filtering.conf** for VM access ports (ephemeral) vlan management

Scripts `vlan_filterring.py` and `bridge_init.py` are put respectively into `/etc/libvirt/hooks/qemu_started_begin.d` and `/etc/libvirt/hooks/daemon_start.d`.

Optionally a link of `bridge_init.py` for daemon reload is made to `/etc/libvirt/hooks/daemon_reload.d/`. Then the following would work:
```
systemctl reload libvirt
```

**Important note:**
For vlan filtering to work each bridge device has to have the following setting:
``` 
echo 1 > /sys/devices/virtual/net/br0/bridge/vlan_filtering
``` 
It is currently not possible in Centos to set this with sysctl, hence it's done from bridge_init.py script

To perform the netlink operations from python the [pyroute2](https://github.com/svinota/pyroute2) library is used. 

