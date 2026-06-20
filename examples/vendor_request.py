#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: USB device with a vendor request handler that controls LEDs."""

from migen import *

from usb_protocol.types      import USBRequestType
from usb_protocol.emitters    import DeviceDescriptorCollection

from liteusb                  import USBDevice
from liteusb.gateware.interface.utmi           import UTMIInterface
from liteusb.gateware.usb.usb2.request         import USBRequestHandler


class LEDRequestHandler(USBRequestHandler):
    """Simple, example request handler that can control the board's LEDs.

    Accepts vendor request 0 (SET_LEDS) and drives the supplied ``leds`` signal
    from the request data.
    """

    REQUEST_SET_LEDS = 0

    def __init__(self, leds):
        super().__init__()
        self._leds = leds

    def do_finalize(self):
        interface = self.interface
        setup     = self.interface.setup
        leds      = self._leds

        #
        # Vendor request handlers.
        #
        If(setup.type == USBRequestType.VENDOR,
            If(setup.request == self.REQUEST_SET_LEDS,
                # Drive interface outputs for this request
                interface.claim.eq(1),

                # Once the receive is complete, respond with an ACK.
                If(interface.rx_ready_for_response,
                    interface.handshakes_out.ack.eq(1)
                ),

                # If we reach the status stage, send a ZLP.
                If(interface.status_requested,
                    *self.send_zlp()
                )
            )
        )

        # If we have an active data byte, splat it onto the LEDs.
        # (Must be in sync domain, cannot nest sync.usb += inside If body)
        self.sync.usb += If(interface.rx.valid & interface.rx.next,
            leds.eq(interface.rx.payload)
        )


class USBVendorDeviceExample(Module):
    """Simple example of a device that operates via vendor requests.

    Sets LEDs to the value set in vendor request 0.
    """

    def __init__(self, phy):
        self.phy = phy

        # LED signals (standalone mode — no platform pins)
        self.leds = Signal(8)

        #
        # Create our USB device.
        #
        self.submodules.usb = usb = USBDevice(bus=phy)

        # Add our standard control endpoint to the device.
        descriptors = self._create_descriptors()
        control_ep = usb.add_standard_control_endpoint(descriptors)

        # Add our custom request handlers.
        control_ep.add_request_handler(LEDRequestHandler(self.leds))

        # Connect our device by default.
        self.comb += usb.connect.eq(1)

    def _create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0001
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "Fancy USB-Controlled LEDs"
            d.iSerialNumber      = "1234"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x01
                    e.wMaxPacketSize   = 64

        return descriptors


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB Vendor Request Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='vendor_request.v', help='Output filename')
    args = parser.parse_args()

    if args.build:
        from liteusb.gateware.interface.utmi import UTMIInterface
        from migen.fhdl.verilog import convert

        dut = USBVendorDeviceExample(UTMIInterface())
        ios = {dut.leds}
        convert(dut, ios, name="usb_vendor_device").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
