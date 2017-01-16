#!/usr/bin/python

import sys
import argparse
import ConfigParser
from pyroute2 import IPRoute

parser = argparse.ArgumentParser(description='Prepare VLAN settings on Linux Bridge interfaces for KVM')
parser.add_argument('phase', nargs='?',
					help='Libvirt event. Defaults to "manual"',
					default='manual')
parser.add_argument('conf_file', nargs='?',
					help='Config file, defaults to /etc/libvirt/hooks/conf/bridge_init.conf',
					default='/etc/libvirt/hooks/conf/bridge_init.conf')
ARGV = parser.parse_args()


def manage_vlans(ipr, port_index, vids, action='add', pvid=False, self=False):
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
		print 'Cannot set multiple PVIDs %s on interface' % str(vids)
		return False
	else:
		if pvid:
			flags = 6
		for tag in vids:
			if self:
				ipr.vlan_filter(action,
								index=port_index,
								vlan_info={'vid': tag, 'flags': flags},
								vlan_flags="self")
			else:
				ipr.vlan_filter(action,
								index=port_index,
								vlan_info={'vid': tag, 'flags': flags})
		return True


def clear_vlan_info(port_vlans_raw):
	'''Parses vlan ids and flags from IFLA_BRIDGE_VLAN_INFO.
	Args:
		port_vlans_raw = ipr.get_vlans(index=idx)
	Returns:
		{'tagged': [<vids>], 'untagged': [<vids>], 'not_clear': [<vids>]}
	'''
	port_tags = {'tagged': [], 'untagged': [], 'not_clear': []}

	if not port_vlans_raw:
		return port_tags

	try:
		ifla_af_spec = port_vlans_raw[0].get_attr('IFLA_AF_SPEC')
		if not ifla_af_spec:
			return port_tags

		bridge_vlan_info = ifla_af_spec.get_attrs('IFLA_BRIDGE_VLAN_INFO')
		if not ifla_af_spec:
			return port_tags
	except AttributeError:
		return port_tags

	# BRIDGE_VLAN_INFO_MASTER = 0x1       # operate on bridge device
	# BRIDGE_VLAN_INFO_PVID = 0x2         # ingress untagged
	# BRIDGE_VLAN_INFO_UNTAGGED = 0x4     # egress untagged
	# BRIDGE_VLAN_INFO_RANGE_BEGIN = 0x8  # range start
	# BRIDGE_VLAN_INFO_RANGE_END = 0x10   # range end
	# BRIDGE_VLAN_INFO_BRENTRY = 0x20     # global bridge vlan entry
	# In linux bridge you can have separate vlan tags for PVID on ingress
	# and egress traffic. Former is called "pvid", latter - "untagged".
	# Both have respective options in "bridge vlan" command.
	# We'll simplify things by always having "pvid" and "untagged" set on one
	# and only one VID, which we should call PVID

	for vlan in bridge_vlan_info:
		# if both INFO_PVID and INFO_UNTAGGED flag are set, then it's a PVID for us
		if vlan['flags'] == 6:
			# there actually can be only one
			port_tags['untagged'].append(vlan['vid'])
		# check for pure tagged VIDs
		elif vlan['flags'] == 0:
			port_tags['tagged'].append(vlan['vid'])
		# we don't want to mess around with ranges and other flags.
		# We'll remove them later. Not properly set PVIDs fall in here too
		else:
			port_tags['not_clear'].append(vlan['vid'])
	return port_tags


def main(ARGV):
	conf_file = ARGV.conf_file

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
				br_tags = filter(None, bridge_conf.get(bridge, 'br-tags').split())

				if trunk_tags:
					try:
						trunk_tags = map(int, trunk_tags)
					except ValueError:
						print 'Could not parse VID list %s for trunk-port %s on bridge %s. Skipping' % (bridge_conf.get(bridge, 'trunk-tags'), trunk_port, bridge)
						# Skipping to next bridge
						continue

				if br_tags:
					try:
						br_tags = map(int, br_tags)
					except ValueError:
						print 'Could not parse VID list %s for bridge interface %s. Skipping' % (bridge_conf.get(bridge, 'br-tags'), bridge)
						# Skipping to next bridge
						continue

				port_tags = clear_vlan_info(ipr.get_vlans(index=idx))

				# Our goal while setting VLAN tags is to make VLANs on the trunk port look
				# exactly how the config defines it. Preferably not touching anything if no changes are required.
				# Only two combinations of flags we consider applicable -- 6 and 0.
				# Only one tag can be set as PVID (flags=6)
				print
				print 'Changing VLAN filtering for trunk-port %s on bridge %s' % (trunk_port, bridge)
				print 'Current set: PVID(s) %s, VIDs %s, Flags unclear %s' % (str(port_tags['untagged']),
																			  str(port_tags['tagged']),
																			  str(port_tags['not_clear']))
				print 'New set: PVID %d, VIDs: %s' % (trunk_pvid, str(trunk_tags))

				# Set our PVID if not already set
				if trunk_pvid not in port_tags['untagged']:
					print 'Setting PVID %d' % trunk_pvid
					manage_vlans(ipr, idx, [trunk_pvid], 'add', pvid=True)

				# Remove all PVIDs (There must be only one, really) if it's not the PVID we've just set
				pvids_to_remove = [ tag for tag in port_tags['untagged'] if tag != trunk_pvid ]
				if pvids_to_remove:
					print 'Deleting PVIDs %s' % str(pvids_to_remove)
					manage_vlans(ipr, idx, pvids_to_remove, 'del')

				# Remove all "not_clear" VIDs
				unclear_to_remove = [ tag for tag in port_tags['not_clear'] if tag != trunk_pvid ]
				if unclear_to_remove:
					print 'Deleting VIDs %s because of unclear flags' % str(unclear_to_remove)
					manage_vlans(ipr, idx, unclear_to_remove, 'del')

				# Remove all VIDs which are not in our config for this bridge's trunk-port
				vids_to_remove = [ tag for tag in port_tags['tagged'] if tag not in trunk_tags]
				if vids_to_remove:
					print 'Deleting VIDs %s' % str(vids_to_remove)
					manage_vlans(ipr, idx, vids_to_remove, 'del')

				# add the VIDs from the config if they are not already set
				# note, some of the tags from port_tags['tagged'] might be gone by now,
				# but those were not in vids anyway
				vids_to_add = [ tag for tag in trunk_tags if tag not in port_tags['tagged']]
				if vids_to_add:
					print 'Setting VIDs %s' % str(vids_to_add)
					manage_vlans(ipr, idx, vids_to_add, 'add')


				# Setting PVID for bridge interface itself
				# Highly unlikely that anybody would ever need to tag the bridge interface itself.
				# It's also a bit dangerous to change PVID settings on it, because wrong ones
				# might make the host unreachable upon libvirt reload.
				# Typically you'd like to have PVID of the bridge interface to be the same as on the trunk
				# Let's check what is currently set as PVID
				idx = ipr.link_lookup(ifname=bridge)[0]
				port_tags = clear_vlan_info(ipr.get_vlans(index=idx))
				print 'Setting VLANs for bridge interface itself (%s)' % bridge
				print 'Current set: PVID(s) %s, VIDs %s, Flags unclear %s' % (str(port_tags['untagged']),
																			  str(port_tags['tagged']),
																			  str(port_tags['not_clear']))
				print 'New set: PVID %d, VIDs: %s' % (br_pvid, str(trunk_tags))

				# Set our PVID if not already set
				if br_pvid not in port_tags['untagged']:
					print 'Setting PVID %d' % br_pvid
					manage_vlans(ipr, idx, [br_pvid], 'add', pvid=True, self=True)

				# Remove all PVIDs (There must be only one, really) if it's not the PVID we've just set
				pvids_to_remove = [ tag for tag in port_tags['untagged'] if tag != br_pvid ]
				if pvids_to_remove:
					print 'Deleting PVIDs %s' % str(pvids_to_remove)
					manage_vlans(ipr, idx, pvids_to_remove, 'del', self=True)

				# Remove all "not_clear" VIDs
				unclear_to_remove = [ tag for tag in port_tags['not_clear'] if tag != br_pvid ]
				if unclear_to_remove:
					print 'Deleting VIDs %s because of unclear flags' % str(unclear_to_remove)
					manage_vlans(ipr, idx, unclear_to_remove, 'del', self=True)

				# Remove all VIDs which are not in our config for this bridge's trunk-port
				vids_to_remove = [ tag for tag in port_tags['tagged'] if tag not in br_tags]
				if vids_to_remove:
					print 'Deleting VIDs %s' % str(vids_to_remove)
					manage_vlans(ipr, idx, vids_to_remove, 'del', self=True)

				# add the VIDs from the config if they are not already set
				# note, some of the tags from port_tags['tagged'] might be gone by now,
				# but those were not in vids anyway
				vids_to_add = [ tag for tag in br_tags if tag not in port_tags['tagged']]
				if vids_to_add:
					print 'Setting VIDs %s' % str(vids_to_add)
					manage_vlans(ipr, idx, vids_to_add, 'add', self=True)
				print


if __name__ == '__main__':
	main(ARGV)
