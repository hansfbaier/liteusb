#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Clock domain parameterization of the USB device stack.

Every class that binds a clock domain takes a ``domain`` parameter (default ``"usb"``) and
relocates its own clock domain(s) to that name, so that integrators can run the stack in a
differently-named domain (e.g. LiteX's ``usb_12``) without patching LiteUSB.

These tests guard the relocation: elaborating with a non-default ``domain`` must leave no trace of
the canonical ``"usb"`` domain, which is what a forgotten hardcoded domain name would show up as.
"""

import unittest

from migen import *
from migen.fhdl.tools import list_clock_domains_expr

from usb_protocol.emitters import DeviceDescriptorCollection

from liteusb.gateware.usb.usb2.device                          import USBDevice
from liteusb.gateware.usb.usb2.endpoints.stream                import USBStreamInEndpoint, USBStreamOutEndpoint
from liteusb.gateware.usb.usb2.endpoints.stream                import USBMultibyteStreamInEndpoint
from liteusb.gateware.usb.usb2.endpoints.isochronous_stream_in import USBIsochronousStreamInEndpoint
from liteusb.gateware.usb.usb2.endpoints.isochronous_stream_out import USBIsochronousStreamOutEndpoint
from liteusb.gateware.usb.usb2.endpoints.status                import USBSignalInEndpoint
from liteusb.gateware.usb.usb2.endpoints.isochronous           import USBIsochronousInEndpoint
from liteusb.gateware.usb.stream                                import USBOutStreamBoundaryDetector
from liteusb.gateware.interface.utmi                           import UTMIInterface

CANONICAL_DOMAIN = "usb"
TEST_DOMAIN      = "usb_12"


def clock_domains(module):
    """ Set of clock domains used by an elaborated module. """
    return set(list_clock_domains_expr(module.get_fragment()))


def descriptors():
    """ A minimal descriptor collection (a device descriptor is required to build the ROM). """
    collection = DeviceDescriptorCollection()
    with collection.DeviceDescriptor() as d:
        d.idVendor           = 0x1209
        d.idProduct          = 0x0001
        d.bNumConfigurations = 1
    return collection


class ClockDomainParameterizationTest(unittest.TestCase):
    """ :meta private: """

    def assert_relocates(self, build, what):
        """ Checks that the default is unchanged and that relocation leaves no canonical domain. """
        # Default: unchanged behaviour.
        self.assertEqual(clock_domains(build(CANONICAL_DOMAIN)), {CANONICAL_DOMAIN}, what)

        # Relocated: the requested domain is used and no hardcoded canonical domain is left behind.
        # (Domains other than the requested one are legitimate: e.g. the "sys" side of a CDC.)
        relocated = clock_domains(build(TEST_DOMAIN))
        self.assertIn(TEST_DOMAIN, relocated, what)
        self.assertNotIn(CANONICAL_DOMAIN, relocated,
            f"{what}: clock domain(s) not relocated: {sorted(relocated)}")

    def device(self, domain, avoid_blockram=False):
        dut = USBDevice(bus=UTMIInterface(), handle_clocking=False, domain=domain)
        dut.add_standard_control_endpoint(descriptors(), avoid_blockram=avoid_blockram)
        dut.add_endpoint(USBStreamInEndpoint(endpoint_number=3, max_packet_size=512, domain=domain))
        dut.add_endpoint(USBStreamOutEndpoint(endpoint_number=3, max_packet_size=512, domain=domain))
        dut.add_endpoint(USBMultibyteStreamInEndpoint(byte_width=2, endpoint_number=6, max_packet_size=512, domain=domain))
        dut.add_endpoint(USBIsochronousStreamInEndpoint(endpoint_number=2, max_packet_size=1024, domain=domain))
        dut.add_endpoint(USBIsochronousStreamOutEndpoint(endpoint_number=4, max_packet_size=1024, domain=domain))
        dut.add_endpoint(USBIsochronousInEndpoint(endpoint_number=5, max_packet_size=1024, domain=domain))
        dut.add_endpoint(USBSignalInEndpoint(width=8, endpoint_number=1, domain=domain))
        return dut

    def test_device(self):
        """ The whole device (core, control endpoint, standard handler, endpoints) relocates. """
        self.assert_relocates(self.device, "USBDevice")

    def test_device_distributed_descriptors(self):
        """ The distributed descriptor handler (its ROM and stream generator) relocates too. """
        self.assert_relocates(lambda d: self.device(d, avoid_blockram=True),
            "USBDevice + GetDescriptorHandlerDistributed")

    def test_device_relocates_endpoints_added_without_domain(self):
        """ Endpoints added with the default domain follow the device they are added to. """
        def build(domain):
            dut = USBDevice(bus=UTMIInterface(), handle_clocking=False, domain=domain)
            dut.add_endpoint(USBStreamInEndpoint(endpoint_number=3, max_packet_size=512))
            dut.add_endpoint(USBStreamOutEndpoint(endpoint_number=3, max_packet_size=512))
            dut.add_endpoint(USBIsochronousStreamInEndpoint(endpoint_number=2, max_packet_size=1024))
            return dut

        relocated = clock_domains(build(TEST_DOMAIN))
        self.assertNotIn(CANONICAL_DOMAIN, relocated, sorted(relocated))

    def test_standalone_endpoints(self):
        """ Endpoints used on their own (outside a USBDevice) relocate as well. """
        for name, build in [
            ("USBStreamInEndpoint",           lambda d: USBStreamInEndpoint(endpoint_number=3, max_packet_size=512, domain=d)),
            ("USBStreamOutEndpoint",          lambda d: USBStreamOutEndpoint(endpoint_number=3, max_packet_size=512, domain=d)),
            ("USBMultibyteStreamInEndpoint",  lambda d: USBMultibyteStreamInEndpoint(byte_width=2, endpoint_number=6, max_packet_size=512, domain=d)),
            ("USBIsochronousStreamInEndpoint", lambda d: USBIsochronousStreamInEndpoint(endpoint_number=2, max_packet_size=1024, domain=d)),
            ("USBIsochronousStreamOutEndpoint", lambda d: USBIsochronousStreamOutEndpoint(endpoint_number=4, max_packet_size=1024, domain=d)),
            ("USBIsochronousInEndpoint",      lambda d: USBIsochronousInEndpoint(endpoint_number=5, max_packet_size=1024, domain=d)),
            ("USBSignalInEndpoint",           lambda d: USBSignalInEndpoint(width=8, endpoint_number=1, domain=d)),
            ("USBOutStreamBoundaryDetector",  lambda d: USBOutStreamBoundaryDetector(domain=d)),
        ]:
            self.assert_relocates(build, name)
