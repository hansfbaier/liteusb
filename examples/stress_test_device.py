#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Example: stress-test endpoint that streams a constant byte at maximum rate.

Defaults to High Speed (512-byte bulk packets).  Set ``LITEUSB_FULL_SPEED=1``
to target Full Speed (64-byte bulk packets).
"""

import os
from migen import *

from usb_protocol.emitters import DeviceDescriptorCollection
from migen.genlib.fsm import FSM, NextState, NextValue

from liteusb                  import USBDevice
from liteusb.gateware.interface.utmi                          import UTMIInterface
from liteusb.gateware.usb.usb2.endpoint                       import EndpointInterface


_full_speed = bool(int(os.getenv('LITEUSB_FULL_SPEED', '0')))
BULK_ENDPOINT_NUMBER = 1
MAX_BULK_PACKET_SIZE = 64 if _full_speed else 512
CONSTANT_TO_SEND     = 0x00


class StressTestEndpoint(Module):
    """Endpoint interface that transmits a constant to the host, without buffering.

    Attributes
    ----------
    interface: EndpointInterface
        Communications link to our USB device.

    Parameters
    ----------
    endpoint_number: int
        The endpoint number (not address) this endpoint should respond to.
    max_packet_size: int
        The maximum packet size for this endpoint. Should match the
        wMaxPacketSize provided in the USB endpoint descriptor.
    constant: int, between 0 and 255
        The constant byte to send.
    """

    def __init__(self, *, endpoint_number, max_packet_size, constant):
        self._endpoint_number = endpoint_number
        self._max_packet_size = max_packet_size
        self._constant        = constant

        #
        # I/O port
        #
        self.interface = EndpointInterface()

    def do_finalize(self):
        interface = self.interface
        tokenizer = interface.tokenizer
        tx        = interface.tx

        # Counter that stores how many bytes we have left to send.
        bytes_to_send = Signal(max=self._max_packet_size + 1, reset=0)

        # True iff we're the active endpoint.
        endpoint_selected = (
            tokenizer.is_in &
            (tokenizer.endpoint == self._endpoint_number)
        )

        # Pulses when the host is requesting a packet from us.
        packet_requested = (
            endpoint_selected
            & tokenizer.ready_for_response
        )

        # FSM: wait for IN token, send packet, wait for ACK, repeat.
        # This mirrors USBInTransferManager but without buffering — we
        # generate data on the fly.
        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # IDLE: wait for IN token, then immediately start sending.
        # We always have data ready (constant streamer), so never NAK.
        fsm.act("IDLE",
            If(packet_requested,
                NextValue(bytes_to_send, self._max_packet_size),
                NextState("SEND_PACKET")
            )
        )

        # SEND_PACKET: transmit the constant payload.
        fsm.act("SEND_PACKET",
            tx.valid.eq(1),
            tx.payload.eq(self._constant),
            tx.first.eq(bytes_to_send == self._max_packet_size),
            tx.last.eq(bytes_to_send == 1),

            If(tx.ready,
                NextValue(bytes_to_send, bytes_to_send - 1),
                If(bytes_to_send == 1,
                    NextState("WAIT_FOR_ACK")
                )
            )
        )

        # WAIT_FOR_ACK: wait for host ACK, then toggle PID and go back.
        fsm.act("WAIT_FOR_ACK",
            If(interface.handshakes_in.ack & endpoint_selected,
                NextState("IDLE")
            ).Elif(tokenizer.new_token,
                # Host didn't ACK — retransmit the same packet.
                NextValue(bytes_to_send, self._max_packet_size),
                NextState("SEND_PACKET")
            )
        )

        # Toggle only bit 0 of our data pid (0=DATA0, 1=DATA1) on ACK.
        # Inverting the whole 2-bit signal maps 0->3 (DATAM) which is wrong.
        self.sync.usb += If(interface.handshakes_in.ack & endpoint_selected,
            interface.tx_pid_toggle[0].eq(~interface.tx_pid_toggle[0])
        )


class USBStressTest(Module):
    """Simple device with a custom endpoint that stress tests USB hardware.

    Uses no buffering — every time the host requests data, we directly
    provide a constant value at maximum possible rate.
    The constant 0x00 sends a stream with maximum NRZI transition rate.
    """

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

        # Add our stress-test endpoint.
        test_ep = StressTestEndpoint(
            endpoint_number=BULK_ENDPOINT_NUMBER,
            max_packet_size=MAX_BULK_PACKET_SIZE,
            constant=CONSTANT_TO_SEND
        )
        usb.add_endpoint(test_ep)

        # Connect our device as a high speed device by default.
        self.comb += [
            usb.connect          .eq(1),
            usb.full_speed_only  .eq(int(os.getenv('LITEUSB_FULL_SPEED', '0'))),

            self.tx_activity_led .eq(usb.tx_activity_led),
            self.rx_activity_led .eq(usb.rx_activity_led),
        ]

    def _create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209
            d.idProduct          = 0x0006
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "Stress Test"
            d.iSerialNumber      = "no serial"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = MAX_BULK_PACKET_SIZE

        return descriptors


def main():
    import sys
    if '--deca' in sys.argv:
        sys.argv.remove('--deca')
        from terasic_deca_common import DecaUSBSoC, deca_main
        class _DecaSoC(DecaUSBSoC):
            def add_usb_device(self, ulpi):
                self.submodules.dev = USBStressTest(ulpi, handle_clocking=False)
                self.usb = self.dev.usb
        deca_main(_DecaSoC, "LiteUSB Stress Test Device on Terasic DECA")
        return

    import argparse
    parser = argparse.ArgumentParser(description="LiteUSB Stress Test Example")
    parser.add_argument('--build', action='store_true', help='Generate Verilog')
    parser.add_argument('--output', default='stress_test_device.v', help='Output filename')
    args = parser.parse_args()

    if args.build:
        from migen.fhdl.verilog import convert
        dut = USBStressTest(UTMIInterface())
        ios = {dut.tx_activity_led, dut.rx_activity_led}
        convert(dut, ios, name="usb_stress_test").write(args.output)
        print(f"Done! Output written to {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
