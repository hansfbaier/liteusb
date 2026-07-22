#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: bulk-IN endpoint that sends a monotonic counter to the host.

Defaults to High Speed (512-byte bulk packets).  Set ``LITEUSB_FULL_SPEED=1``
to target Full Speed (64-byte bulk packets).
"""

import os
from migen import *

from usb_protocol.emitters import DeviceDescriptorCollection

from liteusb                  import USBDevice
from liteusb.gateware.interface.utmi                          import UTMIInterface
from liteusb.gateware.usb.usb2.endpoints.stream               import USBStreamInEndpoint


class USBCounterDeviceExample(Module):
    """Simple device that demonstrates use of a bulk-IN endpoint.

    Always sends a monotonically-incrementing 8-bit counter up to the host.
    """

    BULK_ENDPOINT_NUMBER = 1

    def __init__(self, phy):
        self.phy = phy

        full_speed = bool(int(os.getenv('LITEUSB_FULL_SPEED', '0')))
        max_packet_size = 64 if full_speed else 512

        # Activity LEDs
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()

        #
        # Create our USB device.
        #
        self.submodules.usb = usb = USBDevice(bus=phy, handle_clocking=False)

        # Add our standard control endpoint.
        descriptors = self._create_descriptors(max_packet_size)
        usb.add_standard_control_endpoint(descriptors)

        # Add a stream endpoint to our device.
        stream_ep = USBStreamInEndpoint(
            endpoint_number=self.BULK_ENDPOINT_NUMBER,
            max_packet_size=max_packet_size
        )
        usb.add_endpoint(stream_ep)

        # Always generate a monotonic count for our stream, which counts every time our
        # stream endpoint accepts a data byte.
        counter = Signal(8)
        self.sync.usb += If(stream_ep.stream.ready, counter.eq(counter + 1))

        self.comb += [
            stream_ep.stream.valid   .eq(1),
            stream_ep.stream.payload .eq(counter),

            usb.connect               .eq(1),
            usb.full_speed_only       .eq(int(full_speed)),

            self.tx_activity_led      .eq(usb.tx_activity_led),
            self.rx_activity_led      .eq(usb.rx_activity_led),
        ]

    def _create_descriptors(self, max_packet_size):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0002
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "Counter/Throughput Test"
            d.iSerialNumber      = "no serial"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | self.BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = max_packet_size

        return descriptors


def main():
    import sys
    if '--deca' in sys.argv:
        sys.argv.remove('--deca')
        from terasic_deca_common import DecaUSBSoC, deca_main
        class _DecaSoC(DecaUSBSoC):
            def add_usb_device(self, ulpi):
                self.submodules.dev = USBCounterDeviceExample(ulpi)
                self.usb = self.dev.usb
        deca_main(_DecaSoC, "LiteUSB Counter Device on Terasic DECA")
        return

    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB Counter Device Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='counter_device.v', help='Output filename')
    parser.add_argument('--hierarchical-verilog', action='store_true', help='Enable hierarchical Verilog generation.')
    parser.add_argument('--keep-hierarchy', action='store_true', help='Hierarchical Verilog: keep internal hierarchy.')
    args = parser.parse_args()

    if args.build:
        from migen.fhdl.verilog import convert
        dut = USBCounterDeviceExample(UTMIInterface())
        ios = {dut.tx_activity_led, dut.rx_activity_led}
        if args.hierarchical_verilog:
            from litex.gen.fhdl.verilog import convert as litex_convert
            from litex.gen import LiteXContext
            LiteXContext.top = dut
            hierarchical = args.hierarchical_verilog
            if args.keep_hierarchy:
                hierarchical = {"enabled": True, "keep_hierarchy": True}
            litex_convert(dut, ios, name="usb_counter_device", hierarchical=hierarchical).write(args.output)
        else:
            convert(dut, ios, name="usb_counter_device").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
