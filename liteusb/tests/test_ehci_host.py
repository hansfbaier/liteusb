#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI host tests — HostResetSequencer speed detection and
    TransactionTranslator FS/LS transaction execution.

Tests cover (USB 2.0 §7.1.7.3, §7.1.7.5):
  - HostResetSequencer: SE0 bus reset via NON_DRIVING op-mode,
    FS detect (J), LS detect (persistent K), HS chirp handshake
    (device chirp K → host K/J pairs → HS terminations)
  - TransactionTranslator: token output, data phase, handshake decode,
    busy/sof_hold behavior
"""

from liteusb.tests.test_case import LiteUSBUSBTestCase, usb_domain_test_case
from liteusb.gateware.usb.usb2                 import USBPacketID, USBSpeed
from liteusb.gateware.usb.usb2.host.reset_host import HostResetSequencer
from liteusb.gateware.usb.usb2.host.transaction_translator import TransactionTranslator
from liteusb.gateware.interface.utmi import (
    UTMIOperatingMode, UTMITerminationSelect,
)


class HostResetSequencerTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = HostResetSequencer

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    # UTMI line states
    SE0 = 0b00
    J   = 0b01
    K   = 0b10

    def instantiate_dut(self):
        dut = super().instantiate_dut()
        # Squash timing for fast simulation.
        # NOTE: the timer is sized by _CYCLES_10_MS, so the 10ms value
        # must stay the largest of the three.
        dut._CYCLES_10_MS  = 30
        dut._CYCLES_2P5_MS = 8
        dut._CYCLES_7_MS   = 20
        dut._CHIRP_CYCLES  = 3
        return dut

    def run_reset(self, cycles):
        """ Pulse start and run the sequencer for the given cycles. """
        dut = self.dut
        yield dut.start.eq(1)
        yield dut.bus_busy.eq(0)
        yield
        yield dut.start.eq(0)
        for _ in range(cycles):
            yield

    def wait_done(self, cycles=100):
        """ Run the sequencer until the done pulse is observed. """
        for _ in range(cycles):
            yield
            if (yield self.dut.done):
                return True
        return False

    @usb_domain_test_case
    def test_reset_drives_se0_via_non_driving(self):
        """ Bus reset: transmitter in NON_DRIVING mode (SE0 via pull-downs)
            for the full reset duration.  USB 2.0 §7.1.7.3. """
        dut = self.dut
        yield dut.line_state.eq(self.J)
        yield dut.start.eq(1)
        yield
        yield dut.start.eq(0)
        yield
        yield  # outputs are registered: visible one cycle after state entry

        # During DRIVE_RESET: op_mode=NON_DRIVING, bus_reset=1
        for _ in range(dut._CYCLES_10_MS - 3):
            self.assertEqual((yield dut.op_mode), UTMIOperatingMode.NON_DRIVING)
            self.assertEqual((yield dut.bus_reset), 1)
            self.assertEqual((yield dut.tx_valid), 0)
            yield

    @usb_domain_test_case
    def test_detect_full_speed(self):
        """ J state after reset release → Full-Speed.  USB 2.0 §7.1.7.5. """
        dut = self.dut
        yield dut.line_state.eq(self.J)
        yield dut.start.eq(1)
        yield
        yield dut.start.eq(0)
        self.assertTrue((yield from self.wait_done(dut._CYCLES_10_MS + 20)))
        self.assertEqual((yield dut.current_speed), USBSpeed.FULL)

    @usb_domain_test_case
    def test_detect_low_speed(self):
        """ Persistent K (> max chirp duration) → Low-Speed. """
        dut = self.dut
        yield dut.line_state.eq(self.K)
        yield dut.start.eq(1)
        yield
        yield dut.start.eq(0)
        self.assertTrue((yield from self.wait_done(
            dut._CYCLES_10_MS + dut._CYCLES_7_MS + 20)))
        self.assertEqual((yield dut.current_speed), USBSpeed.LOW)

    @usb_domain_test_case
    def test_hs_chirp_handshake(self):
        """ Device chirp K (1-7ms) → host answers K/J pairs → High-Speed.

        Verifies: host drives chirp K first (USB 2.0 §7.1.7.5), then J,
        for the configured number of pairs, then enables HS terminations.
        """
        dut = self.dut
        yield dut.line_state.eq(self.SE0)
        yield dut.start.eq(1)
        yield
        yield dut.start.eq(0)

        # Wait out the bus reset
        for _ in range(dut._CYCLES_10_MS + 4):
            yield

        # Device chirps K for a while (less than the 7ms maximum)
        yield dut.line_state.eq(self.K)
        for _ in range(6):
            yield
        # Device stops chirping
        yield dut.line_state.eq(self.SE0)
        yield

        # Host must now drive chirp K first (USB 2.0 §7.1.7.5)
        saw_host_k = False
        saw_host_j = False
        for _ in range(6 * (dut._CHIRP_CYCLES + 2) + 20):
            yield
            if (yield dut.tx_valid) and (yield dut.tx_data) == 0x00:
                if not saw_host_k:
                    # The very first host chirp must be a K, not a J
                    self.assertFalse(saw_host_j)
                saw_host_k = True
            if (yield dut.tx_valid) and (yield dut.tx_data) == 0xFF:
                # A J is only valid after the first K
                self.assertTrue(saw_host_k)
                saw_host_j = True
            if (yield dut.done):
                break

        self.assertTrue(saw_host_k)
        self.assertTrue(saw_host_j)
        self.assertEqual((yield dut.done), 1)
        self.assertEqual((yield dut.current_speed), USBSpeed.HIGH)
        self.assertEqual((yield dut.hs_detected), 1)
        # After negotiation: HS normal terminations
        self.assertEqual((yield dut.term_select), UTMITerminationSelect.HS_NORMAL)
        self.assertEqual((yield dut.xcvr_select), USBSpeed.HIGH)

    @usb_domain_test_case
    def test_ls_not_confused_with_chirp(self):
        """ An LS pull-up K persists beyond T_DCHIRP and must NOT trigger
            the host chirp response. """
        dut = self.dut
        yield dut.line_state.eq(self.K)
        yield dut.start.eq(1)
        yield
        yield dut.start.eq(0)
        self.assertTrue((yield from self.wait_done(
            dut._CYCLES_10_MS + dut._CYCLES_7_MS + 20)))
        self.assertEqual((yield dut.current_speed), USBSpeed.LOW)
        self.assertEqual((yield dut.hs_detected), 0)


class TransactionTranslatorTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = TransactionTranslator

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def make_request(self, pid=USBPacketID.OUT, address=1, endpoint=0,
                     speed=USBSpeed.FULL):
        dut = self.dut
        yield dut.request_pid.eq(pid)
        yield dut.request_address.eq(address)
        yield dut.request_endpoint.eq(endpoint)
        yield dut.request_speed.eq(speed)
        yield dut.utmi_tx_ready.eq(1)
        yield dut.request_valid.eq(1)
        yield
        yield dut.request_valid.eq(0)

    def capture_token(self, max_cycles=40):
        """ Capture transmitted bytes while utmi_tx_valid is high. """
        dut = self.dut
        bytes_out = []
        for _ in range(max_cycles):
            if (yield dut.utmi_tx_valid):
                bytes_out.append((yield dut.utmi_tx_data))
            yield
            if len(bytes_out) >= 3:
                break
        return bytes_out

    @usb_domain_test_case
    def test_initial_idle(self):
        """ TT starts idle, busy=0, sof_hold=0. """
        self.assertEqual((yield self.dut.busy), 0)
        self.assertEqual((yield self.dut.sof_hold), 0)
        self.assertEqual((yield self.dut.response_done), 0)

    @usb_domain_test_case
    def test_out_token_bytes(self):
        """ OUT request: 3 token bytes with correct PID/ADDR/CRC5. """
        yield from self.make_request(USBPacketID.OUT, address=0x01, endpoint=0)
        bytes_out = yield from self.capture_token()

        self.assertEqual(len(bytes_out), 3)
        self.assertEqual(bytes_out[0], 0xE1)   # OUT PID byte
        self.assertEqual(bytes_out[1], 0x01)   # ADDR=1, ENDP=0
        # byte2: CRC5(0x001)=0x1D → (0x1D<<3)|0 = 0xE8
        self.assertEqual(bytes_out[2], 0xE8)

    @usb_domain_test_case
    def test_phy_switches_to_fs(self):
        """ TT switches the PHY to the requested speed while busy. """
        dut = self.dut
        yield from self.make_request(speed=USBSpeed.LOW)
        yield
        self.assertEqual((yield dut.busy), 1)
        self.assertEqual((yield dut.utmi_xcvr_select), USBSpeed.LOW)
        self.assertEqual((yield dut.sof_hold), 1)

    @usb_domain_test_case
    def test_handshake_ack(self):
        """ ACK byte from device → response_ack. """
        dut = self.dut
        yield from self.make_request(USBPacketID.OUT)
        # push through token + data phases
        yield dut.request_tx_valid.eq(1)
        yield dut.request_tx_last.eq(1)
        yield dut.request_tx_data.eq(0)
        for _ in range(20):
            yield
            if not (yield dut.utmi_tx_valid):
                break
        yield dut.request_tx_valid.eq(0)

        # Device ACKs
        yield dut.utmi_rx_active.eq(1)
        yield dut.utmi_rx_valid.eq(1)
        yield dut.utmi_rx_data.eq(0xD2)
        yield
        yield dut.utmi_rx_valid.eq(0)
        yield dut.utmi_rx_active.eq(0)

        for _ in range(10):
            yield
            if (yield dut.response_done):
                break

        self.assertEqual((yield dut.response_done), 1)
        self.assertEqual((yield dut.response_ack), 1)

    @usb_domain_test_case
    def test_handshake_nak(self):
        """ NAK byte → response_nak. """
        dut = self.dut
        yield from self.make_request(USBPacketID.OUT)
        yield dut.request_tx_valid.eq(1)
        yield dut.request_tx_last.eq(1)
        for _ in range(20):
            yield
            if not (yield dut.utmi_tx_valid):
                break
        yield dut.request_tx_valid.eq(0)

        yield dut.utmi_rx_active.eq(1)
        yield dut.utmi_rx_valid.eq(1)
        yield dut.utmi_rx_data.eq(0x5A)
        yield
        yield dut.utmi_rx_valid.eq(0)
        yield dut.utmi_rx_active.eq(0)

        for _ in range(10):
            yield
            if (yield dut.response_done):
                break

        self.assertEqual((yield dut.response_nak), 1)

    @usb_domain_test_case
    def test_handshake_stall(self):
        """ STALL byte → response_stall. """
        dut = self.dut
        yield from self.make_request(USBPacketID.OUT)
        yield dut.request_tx_valid.eq(1)
        yield dut.request_tx_last.eq(1)
        for _ in range(20):
            yield
            if not (yield dut.utmi_tx_valid):
                break
        yield dut.request_tx_valid.eq(0)

        yield dut.utmi_rx_active.eq(1)
        yield dut.utmi_rx_valid.eq(1)
        yield dut.utmi_rx_data.eq(0x1E)
        yield
        yield dut.utmi_rx_valid.eq(0)
        yield dut.utmi_rx_active.eq(0)

        for _ in range(10):
            yield
            if (yield dut.response_done):
                break

        self.assertEqual((yield dut.response_stall), 1)

    @usb_domain_test_case
    def test_timeout_gives_error(self):
        """ No response within the timeout → response_error. """
        dut = self.dut
        yield from self.make_request(USBPacketID.IN, speed=USBSpeed.FULL)

        for _ in range(dut._TIMEOUT_FS + 40):
            yield
            if (yield dut.response_done):
                break

        self.assertEqual((yield dut.response_done), 1)
        self.assertEqual((yield dut.response_error), 1)

    @usb_domain_test_case
    def test_phy_restored_to_hs_after_completion(self):
        """ After the transaction, the PHY is switched back to HS. """
        dut = self.dut
        yield from self.make_request(USBPacketID.IN, speed=USBSpeed.FULL)

        for _ in range(dut._TIMEOUT_FS + 40):
            yield
            if (yield dut.response_done):
                break

        self.assertEqual((yield dut.utmi_xcvr_select), USBSpeed.HIGH)
        self.assertEqual((yield dut.utmi_term_select),
                         UTMITerminationSelect.HS_NORMAL)
