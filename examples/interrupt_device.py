#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: USB device with an interrupt endpoint that reports a counter value."""

import os
from migen import *

from usb_protocol.types      import USBTransferType
from usb_protocol.emitters    import DeviceDescriptorCollection

from liteusb                  import USBDevice
from liteusb.gateware.interface.utmi                          import UTMIInterface
from liteusb.gateware.usb.usb2.endpoints.status               import USBSignalInEndpoint


class USBInterruptExample(Module):
    """Simple example of a USB device that presents an interrupt endpoint.

    Creates a 32-bit counter and reports its value each time the interrupt
    endpoint is polled.
    """

    def __init__(self, phy, handle_clocking=True):
        self.phy = phy

        # Activity signals
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()

        # Create the 32-bit counter we'll be using as our status signal.
        counter = Signal(32)
        self.sync.usb += counter.eq(counter + 1)

        #
        # Create our USB device.
        #
        self.submodules.usb = usb = USBDevice(bus=phy, handle_clocking=handle_clocking)

        # Add our standard control endpoint.
        descriptors = self._create_descriptors()
        usb.add_standard_control_endpoint(descriptors)

        # Create an interrupt endpoint that will carry the value of our counter
        # to the host each time our interrupt EP is polled.
        status_ep = USBSignalInEndpoint(width=32, endpoint_number=1, endianness="big")
        usb.add_endpoint(status_ep)
        self.comb += status_ep.signal.eq(counter)

        # Connect our device as a high speed device by default.
        self.comb += [
            usb.connect           .eq(1),
            usb.full_speed_only   .eq(1 if os.getenv('LITEUSB_FULL_SPEED', '0') else 0),

            self.tx_activity_led  .eq(usb.tx_activity_led),
            self.rx_activity_led  .eq(usb.rx_activity_led),
        ]

    def _create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0003
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "Status interrupt mechanism"
            d.iSerialNumber      = "1234"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                # Single IN endpoint, EP1/IN.
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81
                    e.wMaxPacketSize   = 64
                    e.bmAttributes     = USBTransferType.INTERRUPT
                    e.bInterval        = 4

        return descriptors


def main():
    import sys
    if '--deca' in sys.argv:
        sys.argv.remove('--deca')
        from terasic_deca_common import DecaUSBSoC, deca_main
        class _DecaSoC(DecaUSBSoC):
            def add_usb_device(self, ulpi):
                self.submodules.dev = USBInterruptExample(ulpi, handle_clocking=False)
                self.usb = self.dev.usb
        deca_main(_DecaSoC, "LiteUSB Interrupt Device on Terasic DECA")
        return

    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB Interrupt Endpoint Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='interrupt_device.v', help='Output filename')
    args = parser.parse_args()

    if args.build:
        from migen.fhdl.verilog import convert
        dut = USBInterruptExample(UTMIInterface())
        ios = {dut.tx_activity_led, dut.rx_activity_led}
        convert(dut, ios, name="usb_interrupt_device").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
