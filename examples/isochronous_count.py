#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: isochronous IN endpoint that sends a memory-like counter.

Defaults to High Speed (1024 bytes/packet, 3 packets/microframe = 3072 bytes).
Set ``LITEUSB_FULL_SPEED=1`` to target Full Speed (1023 bytes/packet, 1 per frame).
"""

import os
from migen import *

from usb_protocol.types      import USBTransferType
from usb_protocol.emitters    import DeviceDescriptorCollection

from liteusb                  import USBDevice
from liteusb.gateware.interface.utmi                          import UTMIInterface
from liteusb.gateware.usb.usb2.endpoints.isochronous          import USBIsochronousInEndpoint


class USBIsochronousCounterDeviceExample(Module):
    """Simple device that demonstrates use of an isochronous-IN endpoint.

    Sends a monotonically-incrementing 8-bit counter using an isochronous endpoint.
    The counter stands in for a simple memory.
    """

    ISO_ENDPOINT_NUMBER = 1

    def __init__(self, phy, handle_clocking=False):
        self.phy = phy

        full_speed = bool(int(os.getenv('LITEUSB_FULL_SPEED', '0')))

        if full_speed:
            max_packet_size      = 1023
            packets_per_frame    = 1
            w_max_packet_size    = max_packet_size
            bytes_in_frame       = max_packet_size * packets_per_frame
        else:
            max_packet_size      = 1024
            packets_per_microframe = 3
            w_max_packet_size    = ((packets_per_microframe - 1) << 11) | max_packet_size
            bytes_in_frame       = max_packet_size * packets_per_microframe

        self._w_max_packet_size = w_max_packet_size

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

        # Add an isochronous endpoint to our device.
        iso_ep = USBIsochronousInEndpoint(
            endpoint_number=self.ISO_ENDPOINT_NUMBER,
            max_packet_size=max_packet_size
        )
        usb.add_endpoint(iso_ep)

        # Tie our address directly to our value, ensuring that we always
        # count as each offset is increased.
        self.comb += [
            iso_ep.bytes_in_frame .eq(bytes_in_frame),
            iso_ep.value          .eq(iso_ep.address),

            usb.connect           .eq(1),
            usb.full_speed_only   .eq(int(full_speed)),

            self.tx_activity_led  .eq(usb.tx_activity_led),
            self.rx_activity_led  .eq(usb.rx_activity_led),
        ]

    def _create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0005
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "Isochronous IN Test"
            d.iSerialNumber      = "no serial"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bmAttributes     = USBTransferType.ISOCHRONOUS
                    e.bEndpointAddress = 0x80 | self.ISO_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = self._w_max_packet_size
                    e.bInterval        = 1

        return descriptors


def main():
    import sys
    if '--deca' in sys.argv:
        sys.argv.remove('--deca')
        from terasic_deca_common import DecaUSBSoC, deca_main
        class _DecaSoC(DecaUSBSoC):
            def add_usb_device(self, ulpi):
                self.submodules.dev = USBIsochronousCounterDeviceExample(ulpi, handle_clocking=False)
                self.usb = self.dev.usb
        deca_main(_DecaSoC, "LiteUSB Isochronous IN Device on Terasic DECA")
        return

    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB Isochronous IN Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='isochronous_count.v', help='Output filename')
    args = parser.parse_args()

    if args.build:
        from migen.fhdl.verilog import convert
        dut = USBIsochronousCounterDeviceExample(UTMIInterface())
        ios = {dut.tx_activity_led, dut.rx_activity_led}
        convert(dut, ios, name="usb_isochronous_device").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
