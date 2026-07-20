#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: loopback device with bulk-OUT → bulk-IN stream."""

from migen import *

from usb_protocol.emitters import DeviceDescriptorCollection

from liteusb                  import USBDevice
from liteusb.gateware.interface.utmi                          import UTMIInterface
from liteusb.gateware.usb.usb2.endpoints.stream               import USBStreamInEndpoint, USBStreamOutEndpoint


class USBStreamLoopbackExample(Module):
    """Simple device that demonstrates use of bulk-OUT and bulk-IN endpoints.

    Captures streaming OUT data and loops it back to the IN endpoint.
    """

    BULK_ENDPOINT_NUMBER = 1
    # 64 bytes: legal at both FS (max) and HS. Larger values (512) violate
    # the spec at Full Speed and can hang buggy host xHCI controllers.
    MAX_BULK_PACKET_SIZE = 64

    def __init__(self, phy, handle_clocking=True):
        self.phy = phy

        # Activity signals
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()

        #
        # Create our USB device.
        #
        self.submodules.usb = usb = USBDevice(bus=phy, handle_clocking=handle_clocking)

        # Add our standard control endpoint.
        descriptors = self._create_descriptors()
        usb.add_standard_control_endpoint(descriptors)

        # Add stream endpoints.
        self.stream_out_ep = stream_out_ep = USBStreamOutEndpoint(
            endpoint_number=self.BULK_ENDPOINT_NUMBER,
            max_packet_size=self.MAX_BULK_PACKET_SIZE,
        )
        usb.add_endpoint(stream_out_ep)

        self.stream_in_ep = stream_in_ep = USBStreamInEndpoint(
            endpoint_number=self.BULK_ENDPOINT_NUMBER,
            max_packet_size=self.MAX_BULK_PACKET_SIZE,
        )
        usb.add_endpoint(stream_in_ep)

        # Connect our endpoints together — loopback.
        stream_in  = stream_in_ep.stream
        stream_out = stream_out_ep.stream

        self.comb += [
            stream_in.payload  .eq(stream_out.payload),
            stream_in.valid    .eq(stream_out.valid),
            stream_in.first    .eq(stream_out.first),
            stream_in.last     .eq(stream_out.last),
            stream_out.ready   .eq(stream_in.ready),

            usb.connect        .eq(1),

            self.tx_activity_led .eq(usb.tx_activity_led),
            self.rx_activity_led .eq(usb.rx_activity_led),
        ]

    def _create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0004
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "USB Stream Loopback"
            d.iSerialNumber      = "no serial"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = self.BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = self.MAX_BULK_PACKET_SIZE
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | self.BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = self.MAX_BULK_PACKET_SIZE

        return descriptors


def main():
    import sys
    if '--deca' in sys.argv:
        sys.argv.remove('--deca')
        from terasic_deca_common import DecaUSBSoC, deca_main
        class _DecaSoC(DecaUSBSoC):
            def add_usb_device(self, ulpi):
                self.submodules.dev = USBStreamLoopbackExample(ulpi, handle_clocking=False)
                self.usb = self.dev.usb
            def add_user_leds(self):
                # LED4: RX fifo holds committed data (bulk OUT landed)
                # LED5: RX fifo full (data piling up, not drained)
                # LED6: IN side has ever seen stream valid
                # LED7: IN transfer manager has ever been active
                dev = self.dev
                st_valid, st_tx = Signal(2)
                self.sync.usb += [
                    If(dev.stream_in_ep.stream.valid, st_valid.eq(1)),
                    If(dev.stream_in_ep.tx_manager.active, st_tx.eq(1)),
                ]
                self.comb += [
                    self.platform.request("user_led", 4).eq(~(~dev.stream_out_ep.fifo.empty)),
                    self.platform.request("user_led", 5).eq(~dev.stream_out_ep.fifo.full),
                    self.platform.request("user_led", 6).eq(~st_valid),
                    self.platform.request("user_led", 7).eq(~st_tx),
                ]
        deca_main(_DecaSoC, "LiteUSB Stream Loopback on Terasic DECA")
        return

    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB Stream Loopback Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='stream_loopback.v', help='Output filename')
    args = parser.parse_args()

    if args.build:
        from migen.fhdl.verilog import convert
        dut = USBStreamLoopbackExample(UTMIInterface())
        ios = {dut.tx_activity_led, dut.rx_activity_led}
        convert(dut, ios, name="usb_stream_loopback").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
