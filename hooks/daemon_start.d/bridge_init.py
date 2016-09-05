#!/usr/bin/python

import sys
import ConfigParser
from pyroute2 import IPRoute 


def manage_vlans(ipr, port_index, vids, action='add', pvid=False):
	'''Performs vlan_filtering operations on a bridge port.
	Similar to shell comand 'bridge vlan <add|del> dev <port> vid <vid>
	Args:
		ipr - pyroute2.IPRoute() instance
		port_index - int, interface/link index from netlink; member of some bridge
		vids - [int], list of VIDs to add/remove
		action - {add|del} 
		pvid - Boolean, if adding a pvid the vids list should have only one element
	Returns:
		Boolean - True if success
	'''
	flags = 0
	if pvid and len(vids) != 1:
		print 'Cannot set multiple PVIDs %s on interface %s' % (str(vids), port)
		return False
	else:
		if pvid:
			flags = 6
		for tag in vids:
			ipr.vlan_filter(action, index=port_index, vlan_info={'vid': tag, 'flags': flags})
		return True


def main(argv):
	phase = argv[1]
	conf_file = argv[2]

	bridge_conf = ConfigParser.SafeConfigParser(allow_no_value=False)
	with open(conf_file) as f:
		bridge_conf.readfp(f)

	if not bridge_conf.defaults():
		print 'ERROR: could not find defaults section in the config %s' % conf_file
		sys.exit(1)

	ipr = IPRoute()
	links = [x.get_attr('IFLA_IFNAME') for x in ipr.get_vlans()]

	for bridge in bridge_conf.sections():
			
		if bridge not in links:
			print "WARNING: Bridge %s from the config %s is not present. Skipping" % (bridge, conf_file)
		else:
			
			# Enabling bridge vlan filtering for current bridge interface
			# It's kind of important - the entire thing won't work without it
			with open('/sys/devices/virtual/net/%s/bridge/vlan_filtering' % bridge, 'w') as f:
				f.write('1')
			
			trunk_port = bridge_conf.get(bridge, 'trunk-port')
			if not trunk_port:
				print 'WARNING: trunk-port for bridge %s is not set in the config %s. Skipping' % (bridge, conf_file)
			else:
				idx = ipr.link_lookup(ifname=trunk_port)[0]
				trunk_pvid = bridge_conf.getint(bridge, 'trunk-pvid')
				trunk_tags = filter(None, bridge_conf.get(bridge, 'trunk-tags').split())
				br_pvid = bridge_conf.getint(bridge, 'br-pvid')
				if trunk_tags:
					try:
						trunk_tags = map(int, trunk_tags)
					except ValueError:
						print 'Could not parse VID list %s for trunk-port %s on bridge %s' % (bridge_conf.get(bridge, 'trunk-tags'), trunk_port, bridge)
						continue
				# Check which vlan(s) is currently set as PVID/untagged.
				#
				# We'll remove them coz in our world there should be only one PVID.
				# Linux bridge also seems to differ ingres and egress untagged frames:
				# You can tag incoming untagged frames with one VID and 
				# remove tags from outgoing frames having a completely different VID
				# Let's agree that ingress/egress pvid/untagged will be the same VID, aka PVID
				# from pyrote2 source:
				# BRIDGE_VLAN_INFO_MASTER = 0x1       # operate on bridge device
				# BRIDGE_VLAN_INFO_PVID = 0x2         # ingress untagged
				# BRIDGE_VLAN_INFO_UNTAGGED = 0x4     # egress untagged
				# BRIDGE_VLAN_INFO_RANGE_BEGIN = 0x8  # range start
				# BRIDGE_VLAN_INFO_RANGE_END = 0x10   # range end
				# BRIDGE_VLAN_INFO_BRENTRY = 0x20     # global bridge vlan entry
				# So we agreed to take everything with 2nd and 3rd bits set to 1 as PVID. 
				
				# First - get the vlans of current port. Sorry for the mess - it's hell of a structure
				port_tags_raw = filter(lambda x: x['index'] == idx, ipr.get_vlans())[0].get_attr('IFLA_AF_SPEC')
				# now we'll clean it to look like [{'vid': 20, 'flags': 6}, {'vid': 30, 'flags': 0}, ...]
				tags_and_flags = port_tags_raw.get_attrs('IFLA_BRIDGE_VLAN_INFO')

				# and finally like this: port_tags = {'tagged': [20, 30, 40], 'untagged': [2], 'not_clear': []}
				port_tags = {'tagged': [], 'untagged': [], 'not_clear': []}
				for vlan in tags_and_flags:
					# if either INFO_PVID or INFO_UNTAGGED flag is set, then it's a PVID for us
					if (vlan['flags'] & 2) or (vlan['flags'] & 4):
						port_tags['untagged'].append(vlan['vid'])
					# check for pure tagged VIDs
					elif vlan['flags'] == 0:
						port_tags['tagged'].append(vlan['vid'])
					# we don't want to mess around with ranges and othe flags. We'll remove them later
					else: 
						port_tags['not_clear'].append(vlan['vid'])
				
				# Our goal while setting VLAN tags is to make VLANs on the port look
				# exactly how the config defines it. Preferably not touching anything if no changes required.
				# The only two combinations of flags we consider sane are 6 and 0. PVID (flags=6) can be only one
	
				print
				print 'Changing VLAN filtering for trunk-port %s on bridge %s' % (trunk_port, bridge)
				print 'Current set: PVID(s) %s, VIDs %s, Flags unclear %s' % (str(port_tags['untagged']),
																			  str(port_tags['tagged']),
																			  str(port_tags['not_clear']))
				print 'New set: PVID %d, VIDs: %s' % (trunk_pvid, str(trunk_tags))
	
				# Set our PVID with flags INFO_PVID and INFO_UNTAGGED (0x2 and 0x4 = 6) if it's not already set. 
				if trunk_pvid not in port_tags['untagged']:
					print 'Setting PVID %d' % trunk_pvid
					manage_vlans(ipr, idx, [trunk_pvid], 'add', pvid=True)
	
				# Remove all PVIDs with either flag combination (2,4,6 ...) if it's not the PVID we've just set
				pvids_to_remove = [ tag for tag in port_tags['untagged'] if tag != trunk_pvid ]
				if pvids_to_remove:
					print 'Deleting PVIDs %s' % str(pvids_to_remove)
					manage_vlans(ipr, idx, pvids_to_remove, 'del')
	
				# Remove all "not_clear" VIDs
				if port_tags['not_clear']:
					print 'Deleting VIDs %s because of unclear flags' % str(port_tags['not_clear'])
					manage_vlans(ipr, idx, port_tags['not_clear'], 'del')
	
				# Remove all VIDs which are not in our cofig for this bridge's trunk-port
				vids_to_remove = [ tag for tag in port_tags['tagged'] if tag not in trunk_tags]
				if vids_to_remove:
					print 'Deleting VIDs %s' % str(vids_to_remove)
					manage_vlans(ipr, idx, vids_to_remove, 'del')
	
				# add the VIDs from config if they are not already set
				# note, some of the tags from port_tags['tagged'] might be gone by now, 
				# but those were not in vids anyway
				vids_to_add = [ tag for tag in trunk_tags if tag not in port_tags['tagged']]
				if vids_to_add:
					print 'Setting VIDs %s' % str(vids_to_add)
					manage_vlans(ipr, idx, vids_to_add, 'add')

				# Setting PVID for bridge interface itself
				print 'Setting PVID for bridge interface itself (%s)' % bridge
				# Let's check what is currently set as PVID
				idx = ipr.link_lookup(ifname=bridge)[0]
				port_tags_raw = filter(lambda x: x['index'] == idx, ipr.get_vlans())[0].get_attr('IFLA_AF_SPEC')
				tags_and_flags = port_tags_raw.get_attrs('IFLA_BRIDGE_VLAN_INFO')

				# if there are more than one tag and it's not a PVID then something is wrong
				if tags_and_flags[0]['flags'] == 6:
					cur_pvid = tags_and_flags[0]['vid']
					if cur_pvid == br_pvid:
						print 'Leaving PVID of %s unchanged (%d)' % (bridge, br_pvid)
					else:
						print 'Removing PVID %d from %s' % (cur_pvid, bridge)
						manage_vlans(ipr, idx, [cur_pvid], 'del')
						print 'Adding PVID %d to %s' % (br_pvid, bridge)
						manage_vlans(ipr, idx, [cur_pvid], 'add')
				else:
					print 'Something is wrong with PVID of %s. Skipping ...' % bridge

				print	

			
if __name__ == '__main__':
	main(sys.argv)
