#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Full-device loopback integration test.

Wires a bulk-OUT endpoint to a bulk-IN endpoint (like the
stream_out_device example) and verifies data written by the host comes
back verbatim, across multiple rounds with alternating DATA toggles and
with PHY-style rx_valid pauses injected into the OUT packet.

Regression test for the USBOutStreamInterface.stream_eq direction bug
(which prevented bulk-OUT data from ever reaching endpoint FIFOs) and
for the testbench CRC5/CRC16 bugs (which made every device-level test
fail before a byte of gateware was even evaluated).
"""

from liteusb.gateware.usb.usb2.packet import USBPacketID
from liteusb.gateware.usb.usb2.device import USBDevice
from liteusb.gateware.usb.usb2.endpoints.stream import (
    USBStreamInEndpoint, USBStreamOutEndpoint)

from liteusb.tests.usb2       import USBDeviceTest
from liteusb.tests.test_case  import LiteUSBTestCase, usb_domain_test_case
from liteusb.tests.contrib    import usb_packet

from usb_protocol.emitters import DeviceDescriptorCollection


class USBStreamLoopbackTest(USBDeviceTest, LiteUSBTestCase):
    FRAGMENT_UNDER_TEST = USBDevice
    FRAGMENT_ARGUMENTS  = {'handle_clocking': False}

    def __init__(self, methodName='runTest'):
        USBDeviceTest.__init__(self)
        LiteUSBTestCase.__init__(self, methodName)

    def initialize_signals(self):
        yield self.utmi.line_state.eq(0b01)  # FS idle J, no reset
        yield self.dut.connect.eq(1)
        yield self.utmi.tx_ready.eq(1)
        # Let the reset sequencer settle out of INITIALIZE.
        yield from self.advance_cycles(20)

    def provision_dut(self, dut):
        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0004
            d.bNumConfigurations = 1
        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x01
                    e.wMaxPacketSize   = 64
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81
                    e.wMaxPacketSize   = 64
        dut.add_standard_control_endpoint(descriptors)

        self.out_ep = out_ep = USBStreamOutEndpoint(endpoint_number=1, max_packet_size=64)
        dut.add_endpoint(out_ep)
        self.in_ep = in_ep = USBStreamInEndpoint(endpoint_number=1, max_packet_size=64)
        dut.add_endpoint(in_ep)

        dut.comb += [
            in_ep.stream.payload.eq(out_ep.stream.payload),
            in_ep.stream.valid.eq(out_ep.stream.valid),
            in_ep.stream.first.eq(out_ep.stream.first),
            in_ep.stream.last.eq(out_ep.stream.last),
            out_ep.stream.ready.eq(in_ep.stream.ready),
        ]

    def out_transaction_gapped(self, octets, endpoint, data_pid, gap_every=6):
        """OUT transaction with PHY-style rx_valid pauses mid-packet.

        rx_active is held while rx_valid is dropped periodically, as real
        ULPI PHYs throttle the link during reception.
        """
        yield from self.send_token(USBPacketID.OUT, endpoint=endpoint)
        yield from self.interpacket_delay()

        bits  = usb_packet.data_packet(data_pid, octets)
        octs  = self.bits_to_octets(bits)

        yield from self.start_packet(set_rx_valid=True)
        for i, b in enumerate(octs):
            yield from self.provide_byte(b)
            if i % gap_every == gap_every - 1:
                # PHY NXT pause: rx_valid low for a cycle, rx_active stays.
                yield self.utmi.rx_valid.eq(0)
                yield
                yield self.utmi.rx_valid.eq(1)
        yield from self.end_packet()
        yield

        response = yield from self.receive_packet()
        yield from self.interpacket_delay()
        return USBPacketID.from_byte(response)

    @usb_domain_test_case
    def test_loopback_rounds(self):
        yield from self.set_address(10)
        # Give the device time to apply its new address (the harness uses
        # very short interphase delays; a real host waits ~50ms here).
        yield from self.advance_cycles(100)
        yield from self.set_configuration(1)
        yield from self.advance_cycles(20)

        # Multiple rounds, like the hardware test: DATA toggles alternate.
        for round_ in range(4):
            payload  = [(round_ * 64 + i) & 0xFF for i in range(64)]
            data_pid = USBPacketID.DATA0 if round_ % 2 == 0 else USBPacketID.DATA1

            hs = yield from self.out_transaction_gapped(payload, endpoint=1, data_pid=data_pid)
            self.assertEqual(hs, USBPacketID.ACK)

            # The transfer is exactly one max-size packet, so the host reads
            # exactly one packet (retrying NAKs while the IN endpoint's
            # buffer fills from the loopback FIFO).
            pid = USBPacketID.NAK
            while pid == USBPacketID.NAK:
                pid, data = yield from self.in_transaction(endpoint=1, data_pid=data_pid)
            self.assertEqual(pid, data_pid)
            self.assertEqual(data, payload)
