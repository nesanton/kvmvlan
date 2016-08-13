# kvmvlan
vlan management for KVM based on libvirt hooks


Linux bridge can do proper VLAN management, but for some reason people seem to stick to the old way of creating interface.vlan_id  (eth0.100) subinterfaces and making yet another bridge on top of each.

Netlink command `bridge vlan add <dev> vid <id> [pvid] [untagged]` for example can add a vlan tag on any interface participating in a bridge. `bridge vlan show` would show all the tags on all interfaces. There's whole lot of other arguments which make Linux bridges act just like normal managed switches. So why not?

Too bad Centos cannot make these settings persistent across reboots. At least in a nice clean config-files way.
ifup.local can help of course, but not always.

KVM, for instance, with its tap interfaces cannot trully benefit from ifup.local. Tap interfaces are either preset and fed to libvirt with <dev> tags or created at qemu guest start time. The former cannot be persistently configured in Centos (this can go to ifup.local), the latter are somewhat ethemeral. In both cases, assigning vlans tags to them, especially dynamically, should be done after qemy guest start.

The idea of this little project is to have vlan management handled by libvirt itself. With the [libvirt hooks](https://www.libvirt.org/hooks.html) this should not be a problem. 
