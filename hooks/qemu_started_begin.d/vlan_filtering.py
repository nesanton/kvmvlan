#!/usr/bin/python

import sys
import argparse
import ConfigParser
from pyroute2 import IPRoute 

parser = argparse.ArgumentParser(description='Prepare VLAN settings on Linux Bridge interfaces for KVM')
parser.add_argument('vm_name', 
					help='Name of qemu guest')
parser.add_argument('conf_file', nargs='?', 
					help='Config file, defaults to /etc/libvirt/hooks/conf/vlan_filtering.conf', 
					default='/etc/libvirt/hooks/conf/vlan_filtering.conf')
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
		print 'Cannot set multiple PVIDs %s on interface %s' % str(vids)
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
	vm_name = ARGV.vm_name
	conf_file = ARGV.conf_file

	vlans_conf = ConfigParser.SafeConfigParser(allow_no_value=False)
	with open(conf_file) as f:
		vlans_conf.readfp(f)

	if not vlans_conf.defaults():
		print 'ERROR: could not find defaults section in the config %s' % conf_file
		sys.exit(1)

	ipr = IPRoute()
	links = [x.get_attr('IFLA_IFNAME') for x in ipr.get_links()]

	for interface in vlans_conf.sections():
		guest = vlans_conf.get(interface, 'guest')
		# Apply changes only for current guest
		if guest != vm_name:
			print 'Skipping interface %s from other guest (%s)' % (interface, guest)
		else:
			
			# TODO ??: check if the interface is in VM's xml
			
			if interface not in links:
				print "WARNING: Link %s from the config %s is not present. Skipping" % (interface, conf_file)
			else:

				idx = ipr.link_lookup(ifname=interface)[0]
				pvid = vlans_conf.getint(interface, 'pvid')
				vids = filter(None, vlans_conf.get(interface, 'tagged').split())
				if vids:
					print vids
					try:
						vids = map(int, vids)
					except ValueError:
						print 'Could not parse VID list %s for interface %s. Skipping' % (vlans_conf.get(interface, 'tagged'), interface)
						# Skipping to next bridge
						continue
				
				port_tags = clear_vlan_info(ipr.get_vlans(index=idx))
				
				# Our goal while setting VLAN tags is to make VLANs on the interface look
				# exactly how the config defines it. Preferably not touching anything if no changes are required.
				# Only two combinations of flags we consider applicable -- 6 and 0. 
				# Only one tag can be set as PVID (flags=6)
				print
				print 'Changing VLAN filtering for interface %s' % interface
				print 'Current set: PVID(s) %s, VIDs %s, Flags unclear %s' % (str(port_tags['untagged']),
																			  str(port_tags['tagged']),
																			  str(port_tags['not_clear']))
				print 'New set: PVID %d, VIDs: %s' % (pvid, str(vids))
	
				# Set our PVID with flags INFO_PVID and INFO_UNTAGGED (0x2 and 0x4 = 6) if it's not already set. 
				if pvid not in port_tags['untagged']:
					print 'Setting PVID %d' % pvid
					manage_vlans(ipr, idx, [pvid], 'add', pvid=True)
	
				# Remove all PVIDs (There must be only one, really) if it's not the PVID we've just set
				pvids_to_remove = [ tag for tag in port_tags['untagged'] if tag != pvid ]
				if pvids_to_remove:
					print 'Deleting PVIDs %s' % str(pvids_to_remove)
					manage_vlans(ipr, idx, pvids_to_remove, 'del')
	
				# Remove all "not_clear" VIDs
				unclear_to_remove = [ tag for tag in port_tags['not_clear'] if tag != pvid ]
				if unclear_to_remove:
					print 'Deleting VIDs %s because of unclear flags' % str(unclear_to_remove)
					manage_vlans(ipr, idx, unclear_to_remove, 'del')
	
				# Remove all VIDs which are not in our config for this interface
				vids_to_remove = [ tag for tag in port_tags['tagged'] if tag not in vids]
				if vids_to_remove:
					print 'Deleting VIDs %s' % str(vids_to_remove)
					manage_vlans(ipr, idx, vids_to_remove, 'del')
	
				# add the VIDs from the config if they are not already set
				# note, some of the tags from port_tags['tagged'] might be gone by now, 
				# but those were not in 'vids' list anyway
				vids_to_add = [ tag for tag in vids if tag not in port_tags['tagged']]
				if vids_to_add:
					print 'Setting VIDs %s' % str(vids_to_add)
					manage_vlans(ipr, idx, vids_to_add, 'add')

				print
		
			
if __name__ == '__main__':
	main(ARGV)
