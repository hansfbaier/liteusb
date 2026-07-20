#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""UTMITranslator ULPI receive-path test.

Drives the PHY side of the ULPI bus (dir/nxt/data) with a full DATA0
packet, including nxt pauses, and verifies the translated UTMI rx byte
stream arrives intact.
"""

from migen import *

from liteusb.gateware.interface.ulpi import UTMITranslator, ULPIInterface
from liteusb.tests.contrib import usb_packet
from liteusb.tests.utils import LiteUSBUSBTestCase

from usb_protocol.types import USBPacketID


class ULPIRxGapsTest(LiteUSBUSBTestCase):
    GAP_EVERY = 6

    # Custom DUT: translator needs an ULPIInterface, created in setUp.
    def instantiate_dut(self):
        self.ulpi = ULPIInterface()
        return UTMITranslator(ulpi=self.ulpi, handle_clocking=False)

    def test_rx_with_nxt_pauses(self):
        ulpi = self.ulpi
        dut  = self.dut

        payload = list(range(64))
        bits    = usb_packet.data_packet(USBPacketID.DATA0, payload)
        octs    = []
        while bits:
            chunk, bits = bits[:8], bits[8:]
            octs.append(int(chunk[::-1], 2))
        # octs = [PID, payload..., crc_lo, crc_hi]

        received = []

        def init():
            yield ulpi.dir.eq(0)
            yield ulpi.nxt.eq(0)
            yield ulpi.data.i.eq(0)
            for _ in range(300):
                yield
            self.assertEqual(received, octs)

        def phy():
            yield
            # PHY takes the bus: dir up, RxCmd with rxactive=1 for a while.
            yield ulpi.dir.eq(1)
            yield ulpi.data.i.eq(0x10)
            for _ in range(4):
                yield
            # Stream packet bytes, with a nxt pause every GAP_EVERY bytes.
            for i, b in enumerate(octs):
                yield ulpi.data.i.eq(b)
                yield ulpi.nxt.eq(1)
                yield
                if i % self.GAP_EVERY == self.GAP_EVERY - 1:
                    yield ulpi.nxt.eq(0)
                    yield ulpi.data.i.eq(0x10)  # RxCmd, still rxactive
                    yield
            # End of packet.
            yield ulpi.nxt.eq(0)
            yield ulpi.data.i.eq(0x00)
            yield
            yield ulpi.dir.eq(0)
            for _ in range(30):
                yield

        def mon():
            for _ in range(300):
                if (yield dut.rx_valid):
                    received.append((yield dut.rx_data))
                yield

        self.domain = 'usb'
        self._ensure_clocks_present()
        self._sync_processes += [init(), phy(), mon()]
        self.simulate(vcd_suffix='test_rx_with_nxt_pauses')
