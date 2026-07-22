#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Full-device stress-test IN endpoint integration test.

Wires a StressTestEndpoint (constant-streamer, no buffering) into a
USBDevice and verifies that IN transactions return the expected
constant payload with correct data toggling across multiple rounds.

Regression test for the missing-NAK bug: StressTestEndpoint did not
NAK when it had no data ready, causing the host to see no response
(STALL/timeout/EIO on hardware).
"""

from liteusb.gateware.usb.usb2.packet import USBPacketID
from liteusb.gateware.usb.usb2.device import USBDevice

from liteusb.tests.usb2       import USBDeviceTest
from liteusb.tests.test_case  import LiteUSBTestCase, usb_domain_test_case

from usb_protocol.emitters import DeviceDescriptorCollection

import sys
sys.path.insert(0, "examples")
from stress_test_device import StressTestEndpoint


MAX_PACKET_SIZE = 64


class USBStressTestTest(USBDeviceTest, LiteUSBTestCase):
    FRAGMENT_UNDER_TEST = USBDevice
    FRAGMENT_ARGUMENTS  = {'handle_clocking': False}

    def __init__(self, methodName='runTest'):
        USBDeviceTest.__init__(self)
        LiteUSBTestCase.__init__(self, methodName)

    def initialize_signals(self):
        yield self.utmi.line_state.eq(0b01)  # FS idle J, no reset
        yield self.dut.connect.eq(1)
        yield self.utmi.tx_ready.eq(1)
        yield from self.advance_cycles(20)

    def provision_dut(self, dut):
        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0006
            d.bNumConfigurations = 1
        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

        dut.add_standard_control_endpoint(descriptors)

        self.stress_ep = stress_ep = StressTestEndpoint(
            endpoint_number=1,
            max_packet_size=MAX_PACKET_SIZE,
            constant=0x00,
        )
        dut.add_endpoint(stress_ep)

    @usb_domain_test_case
    def test_stress_in(self):
        yield from self.set_address(10)
        yield from self.advance_cycles(100)
        yield from self.set_configuration(1)
        yield from self.advance_cycles(20)

        expected = [0x00] * MAX_PACKET_SIZE
        data_pid = USBPacketID.DATA0

        for round_ in range(4):
            pid = USBPacketID.NAK
            naks = 0
            while pid == USBPacketID.NAK:
                naks += 1
                self.assertLess(naks, self.MAX_NAKS)
                pid, data = yield from self.in_transaction(
                    endpoint=1, data_pid=data_pid)

            self.assertEqual(pid, data_pid)
            self.assertEqual(data, expected)

            data_pid = USBPacketID.DATA1 if data_pid == USBPacketID.DATA0 else USBPacketID.DATA0
