#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
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
    MAX_BULK_PACKET_SIZE = 512

    def __init__(self, phy):
        self.phy = phy

        # Activity signals
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()

        #
        # Create our USB device.
        #
        self.submodules.usb = usb = USBDevice(bus=phy)

        # Add our standard control endpoint.
        descriptors = self._create_descriptors()
        usb.add_standard_control_endpoint(descriptors)

        # Add stream endpoints.
        stream_out_ep = USBStreamOutEndpoint(
            endpoint_number=self.BULK_ENDPOINT_NUMBER,
            max_packet_size=self.MAX_BULK_PACKET_SIZE,
        )
        usb.add_endpoint(stream_out_ep)

        stream_in_ep = USBStreamInEndpoint(
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
            d.idProduct          = 0x0001
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
