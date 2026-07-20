#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: CDC-ACM USB serial device in loopback mode.

Uses the pre-made USBSerialDevice from liteusb.gateware.usb.devices.acm.
"""

from migen import *

from liteusb.gateware.interface.utmi                    import UTMIInterface
from liteusb.gateware.usb.devices.acm                   import USBACMSerialDevice


class USBSerialDeviceExample(Module):
    """Device that acts as a 'USB-to-serial' loopback.

    Uses the pre-made ``USBSerialDevice`` to present a CDC-ACM virtual COM port.
    Data received on OUT is looped back to IN.
    """

    def __init__(self, phy, handle_clocking=True):
        self.phy = phy

        # Activity signals
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()

        #
        # Create our USB-to-serial device.
        #
        self.submodules.usb_serial = usb_serial = USBACMSerialDevice(
            bus=phy,
            idVendor=0x1209,
            idProduct=0x0001,
            handle_clocking=handle_clocking
        )

        # Place the streams into a loopback configuration.
        # sink = data to host (TX), source = data from host (RX)
        self.comb += [
            usb_serial.sink.valid    .eq(usb_serial.source.valid),
            usb_serial.sink.first    .eq(usb_serial.source.first),
            usb_serial.sink.last     .eq(usb_serial.source.last),
            usb_serial.sink.data     .eq(usb_serial.source.data),
            usb_serial.source.ready  .eq(usb_serial.sink.ready),

            # Always connect by default.
            usb_serial.connect       .eq(1),
        ]


def main():
    import sys
    if '--deca' in sys.argv:
        sys.argv.remove('--deca')
        from terasic_deca_common import DecaUSBSoC, deca_main
        class _DecaSoC(DecaUSBSoC):
            def add_usb_device(self, ulpi):
                self.submodules.dev = USBSerialDeviceExample(ulpi, handle_clocking=False)
                self.usb = self.dev.usb_serial.usb
        deca_main(_DecaSoC, "LiteUSB CDC-ACM Serial Loopback on Terasic DECA")
        return

    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB CDC-ACM Serial Loopback Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='acm_serial.v', help='Output filename')
    args = parser.parse_args()

    if args.build:
        from migen.fhdl.verilog import convert
        dut = USBSerialDeviceExample(UTMIInterface())
        ios = {dut.tx_activity_led, dut.rx_activity_led}
        convert(dut, ios, name="usb_acm_serial").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
