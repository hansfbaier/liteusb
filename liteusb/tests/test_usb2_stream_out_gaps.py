#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""USBStreamOutEndpoint robustness to PHY rx_valid pauses.

Feeds a 64-byte payload into the endpoint's rx stream with periodic
rx_valid gaps (as real ULPI PHYs throttle mid-packet) and checks that
exactly the same bytes come out of the endpoint's stream interface.
"""

from migen import *

from liteusb.gateware.usb.usb2.endpoints.stream import USBStreamOutEndpoint
from liteusb.tests.utils import LiteUSBUSBTestCase


class USBStreamOutGapsTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = USBStreamOutEndpoint
    FRAGMENT_ARGUMENTS  = {'endpoint_number': 1, 'max_packet_size': 64}

    PAYLOAD  = list(range(1, 65))
    GAP_EVERY = 5

    def test_gapped_receive(self):
        dut = self.dut
        received = []

        def init():
            yield dut.interface.tokenizer.endpoint.eq(1)
            yield dut.interface.tokenizer.is_out.eq(1)
            yield dut.interface.rx_pid_toggle.eq(0)
            yield dut.stream.ready.eq(1)
            for _ in range(300):
                yield
            self.assertEqual(received, self.PAYLOAD)

        def host():
            yield
            yield dut.interface.rx.valid.eq(1)
            for i, b in enumerate(self.PAYLOAD):
                yield dut.interface.rx.payload.eq(b)
                yield dut.interface.rx.next.eq(1)
                yield
                if i % self.GAP_EVERY == self.GAP_EVERY - 1:
                    # PHY NXT pause: rx_valid low, rx_active (valid) stays.
                    yield dut.interface.rx.next.eq(0)
                    yield
            yield dut.interface.rx.next.eq(0)
            yield dut.interface.rx.valid.eq(0)
            yield dut.interface.rx_complete.eq(1)
            yield
            yield dut.interface.rx_complete.eq(0)
            for _ in range(30):
                yield

        def drain():
            for _ in range(300):
                if (yield dut.stream.valid) and (yield dut.stream.ready):
                    received.append((yield dut.stream.payload))
                yield

        self.domain = 'usb'
        self._ensure_clocks_present()
        self._sync_processes += [init(), host(), drain()]
        self.simulate(vcd_suffix='test_gapped_receive')
