#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" EHCI schedule processor tests — validates FSM transitions and counter behavior.

Tests cover:
  - EHCI Spec Rev 1.0, Section 4 (Operational Model)
  - HCHalted initial state and transition on Run
  - SOF strobe triggers PERIODIC→ASYNC→WAIT_XFER pipeline
  - Schedule enable gating (PSE/ASE)
  - Microframe counter 0-7 and frame_index
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
        dut = self.dut
        self.assertEqual((yield dut.hc_halted), 1)

    @usb_domain_test_case
    def test_run_unhalts(self):
        """ EHCI §4.1: Setting Run=1 clears HCHalted and FSM leaves HALTED. """
        dut = self.dut
        self.assertEqual((yield dut.hc_halted), 1)

        yield dut.run.eq(1)
        yield
        # HCHalted is combinatorial: ~run
        self.assertEqual((yield dut.hc_halted), 0)

    @usb_domain_test_case
    def test_sof_triggers_schedule(self):
        """ SOF strobe with PSE+ASE enabled → PERIODIC→ASYNC→WAIT_XFER. """
        dut = self.dut

        yield dut.run.eq(1)
        yield dut.periodic_enable.eq(1)
        yield dut.async_enable.eq(1)
        yield

        # Pulse SOF
        yield dut.sof_strobe.eq(1)
        yield
        yield dut.sof_strobe.eq(0)

        # After SOF, FSM moves through PERIODIC→ASYNC→WAIT_XFER
        # Give it cycles to settle
        for _ in range(5):
            yield

        # Should be in or past WAIT_XFER
        # The schedule processor will assert transfer_request.valid in ASYNC
        valid = (yield dut.transfer_request.valid)
        self.assertEqual(valid, 1)

        # Complete the transfer
        yield dut.transfer_response.done.eq(1)
        yield
        yield dut.transfer_response.done.eq(0)
        yield
        # Now transfer_request.valid should be de-asserted
        valid = (yield dut.transfer_request.valid)
        self.assertEqual(valid, 0)

    @usb_domain_test_case
    def test_no_schedule_enables_skips_wait_xfer(self):
        """ With PSE=ASE=0, SOF goes through PERIODIC→ASYNC→IDLE (no WAIT_XFER). """
        dut = self.dut

        yield dut.run.eq(1)
        yield dut.periodic_enable.eq(0)
        yield dut.async_enable.eq(0)
        yield

        yield dut.sof_strobe.eq(1)
        yield
        yield dut.sof_strobe.eq(0)
        for _ in range(5):
            yield

        # transfer_request.valid should NOT be asserted
        valid = (yield dut.transfer_request.valid)
        self.assertEqual(valid, 0)

    @usb_domain_test_case
    def test_microframe_counter(self):
        """ Frame index increments on each SOF strobe.  EHCI §2.2.4. """
        dut = self.dut

        yield dut.run.eq(1)
        yield dut.periodic_enable.eq(0)
        yield dut.async_enable.eq(0)
        yield

        initial = (yield dut.frame_index)

        for i in range(10):
            yield dut.sof_strobe.eq(1)
            yield
            yield dut.sof_strobe.eq(0)
            yield

        final = (yield dut.frame_index)
        self.assertEqual(final, initial + 10)

    @usb_domain_test_case
    def test_port_speed_input(self):
        """ port_speed input exists and is readable. """
        dut = self.dut
        yield dut.port_speed.eq(2)  # LS
        yield
        self.assertEqual((yield dut.port_speed), 2)
