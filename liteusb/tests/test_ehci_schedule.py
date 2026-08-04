#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI schedule processor tests — FSM gating and counter behavior.

The schedule engine is currently a stub: it does not yet walk QH/qTD
structures in memory (no DMA master).  These tests pin down the behavior
that does exist:
  - HCHalted tracking of USBCMD.RUN (EHCI §2.2.2)
  - frame_index increments once per microframe/SOF strobe (EHCI §2.2.4)
  - periodic/async schedule status bits follow the enables (EHCI §2.2.2)
  - no transfer request is ever issued (nothing may reach the bus)
"""

from liteusb.tests.test_case import LiteUSBUSBTestCase, usb_domain_test_case
from liteusb.gateware.usb.usb2.host.schedule import EHCIScheduleProcessor


class EHCIScheduleProcessorTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = EHCIScheduleProcessor
    FRAGMENT_ARGUMENTS  = {"num_ports": 1}

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    @usb_domain_test_case
    def test_initial_halted(self):
        """ EHCI §2.2.2: HCHalted=1 when USBCMD.Run=0. """
        self.assertEqual((yield self.dut.hc_halted), 1)

    @usb_domain_test_case
    def test_run_unhalts(self):
        """ Setting Run=1 clears HCHalted. """
        dut = self.dut
        yield dut.run.eq(1)
        yield
        yield
        self.assertEqual((yield dut.hc_halted), 0)
        yield dut.run.eq(0)
        yield
        yield
        self.assertEqual((yield dut.hc_halted), 1)

    @usb_domain_test_case
    def test_frame_index_increments_per_sof(self):
        """ FRINDEX advances by one per microframe (SOF strobe). EHCI §2.2.4. """
        dut = self.dut
        yield dut.run.eq(1)
        yield

        initial = (yield dut.frame_index)
        for _ in range(10):
            yield dut.sof_strobe.eq(1)
            yield
            yield dut.sof_strobe.eq(0)
            yield

        self.assertEqual((yield dut.frame_index), initial + 10)

    @usb_domain_test_case
    def test_frame_index_stops_when_halted(self):
        """ No FRINDEX advance while Run=0. """
        dut = self.dut
        initial = (yield dut.frame_index)
        for _ in range(5):
            yield dut.sof_strobe.eq(1)
            yield
            yield dut.sof_strobe.eq(0)
            yield
        self.assertEqual((yield dut.frame_index), initial)

    @usb_domain_test_case
    def test_periodic_status_pulses(self):
        """ Periodic Schedule Status asserts while the periodic schedule
            is being processed after a SOF.  EHCI §2.2.2 (PSS bit). """
        dut = self.dut
        yield dut.run.eq(1)
        yield dut.periodic_enable.eq(1)
        yield

        yield dut.sof_strobe.eq(1)
        yield
        yield dut.sof_strobe.eq(0)

        seen = 0
        for _ in range(10):
            yield
            if (yield dut.periodic_status):
                seen += 1
        self.assertGreaterEqual(seen, 1)

    @usb_domain_test_case
    def test_async_status_pulses(self):
        """ Async Schedule Status asserts while the async schedule is
            processed after a SOF.  EHCI §2.2.2 (ASS bit). """
        dut = self.dut
        yield dut.run.eq(1)
        yield dut.async_enable.eq(1)
        yield

        yield dut.sof_strobe.eq(1)
        yield
        yield dut.sof_strobe.eq(0)

        seen = 0
        for _ in range(10):
            yield
            if (yield dut.async_status):
                seen += 1
        self.assertGreaterEqual(seen, 1)

    @usb_domain_test_case
    def test_no_periodic_status_when_disabled(self):
        """ PSS stays clear while USBCMD.PSE=0. """
        dut = self.dut
        yield dut.run.eq(1)
        yield dut.periodic_enable.eq(0)
        yield

        yield dut.sof_strobe.eq(1)
        yield
        yield dut.sof_strobe.eq(0)
        for _ in range(10):
            yield
            self.assertEqual((yield dut.periodic_status), 0)

    @usb_domain_test_case
    def test_no_transfer_request_issued(self):
        """ The stub never issues a transfer request (nothing reaches
            the bus until DMA schedule walking exists). """
        dut = self.dut
        yield dut.run.eq(1)
        yield dut.periodic_enable.eq(1)
        yield dut.async_enable.eq(1)
        yield

        for _ in range(20):
            yield dut.sof_strobe.eq(1)
            yield
            yield dut.sof_strobe.eq(0)
            yield
            self.assertEqual((yield dut.transfer_request.valid), 0)
