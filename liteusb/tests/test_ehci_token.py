#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI token generator tests — SOF counter, CRC5, and host token generation.

Tests cover:
  - USBCRC5: USB 2.0 spec §8.3.5 polynomial x^5+x^2+1, verified against
    a bit-serial reference model and known on-the-wire token captures
  - USBSOFCounter: HS 125µs / FS 1ms timing, frame rollover, sof_hold
  - USBHostTokenGenerator: exact 3-byte token output (PID, payload, CRC5)
"""

from migen import Signal

from liteusb.tests.test_case import LiteUSBUSBTestCase, usb_domain_test_case
from liteusb.gateware.usb.usb2           import USBPacketID
from liteusb.gateware.usb.usb2.host.token_generator import (
    USBSOFCounter, USBCRC5, USBHostTokenGenerator,
)


# ── Reference model (USB 2.0 §8.3.5) ────────────────────────────────────────

def crc5_reference(data):
    """ Bit-serial USB token CRC5: init all-ones, LSB-first, complemented. """
    crc = 0x1F
    for i in range(11):
        fb = ((data >> i) & 1) ^ (crc & 1)
        crc >>= 1
        if fb:
            crc ^= 0x14  # reflected x^5+x^2+1
    return (~crc) & 0x1F


def token_bytes_reference(pid, payload):
    """ Full 3-byte token as it appears on the wire. """
    return [
        pid | ((~pid & 0xF) << 4),
        payload & 0xFF,
        ((crc5_reference(payload) & 0x1F) << 3) | (payload >> 8),
    ]


# ── SOF counter ─────────────────────────────────────────────────────────────

class USBSOFCounterTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = USBSOFCounter

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def instantiate_dut(self):
        dut = super().instantiate_dut()
        # Squash periods for fast simulation
        dut._hs_period = 5
        dut._fs_period = 8
        return dut

    @usb_domain_test_case
    def test_hs_sof_period(self):
        """ HS: issue_sof pulses once every hs_period cycles. """
        dut = self.dut
        yield dut.speed.eq(0)  # HS
        yield dut.sof_hold.eq(0)

        pulses = []
        for cycle in range(dut._hs_period * 4):
            yield
            if (yield dut.issue_sof):
                pulses.append(cycle)

        self.assertEqual(len(pulses), 4)
        for a, b in zip(pulses, pulses[1:]):
            self.assertEqual(b - a, dut._hs_period)

    @usb_domain_test_case
    def test_fs_frame_timing(self):
        """ FS: SOF period is fs_period (1ms), no microframe counting. """
        dut = self.dut
        yield dut.speed.eq(1)  # FS
        yield dut.sof_hold.eq(0)

        pulses = 0
        for _ in range(dut._fs_period * 3):
            yield
            if (yield dut.issue_sof):
                pulses += 1

        self.assertEqual(pulses, 3)
        self.assertEqual((yield dut.microframe_number), 0)
        self.assertGreater((yield dut.frame_number), 0)

    @usb_domain_test_case
    def test_hs_microframe_counting(self):
        """ HS: 8 microframes make one frame; frame_number increments then. """
        dut = self.dut
        yield dut.speed.eq(0)
        yield dut.sof_hold.eq(0)

        sof_count = 0
        frames = []
        for _ in range(dut._hs_period * 18):
            yield
            if (yield dut.issue_sof):
                sof_count += 1
                frames.append((yield dut.frame_number))

        # Steady state: 8 SOFs per frame.  The frame number increments
        # on the SOF that wraps the microframe counter 7→0.
        self.assertEqual(frames[0], 0)
        self.assertEqual(frames[7], 1)    # 8th SOF wraps to frame 1
        self.assertEqual(frames[14], 1)
        self.assertEqual(frames[15], 2)   # 16th SOF wraps to frame 2

    @usb_domain_test_case
    def test_sof_hold_defers(self):
        """ sof_hold queues the SOF; it fires immediately on release. """
        dut = self.dut
        yield dut.speed.eq(0)
        yield dut.sof_hold.eq(1)

        for _ in range(dut._hs_period * 3):
            yield
        self.assertEqual((yield dut.issue_sof), 0)

        # Release hold: the queued SOF fires immediately after
        yield dut.sof_hold.eq(0)
        fired = False
        for _ in range(4):
            yield
            if (yield dut.issue_sof):
                fired = True
                break
        self.assertTrue(fired)

    @usb_domain_test_case
    def test_new_frame_strobe(self):
        """ new_frame pulses once per 8 HS microframes. """
        dut = self.dut
        yield dut.speed.eq(0)
        yield dut.sof_hold.eq(0)

        new_frames = 0
        for _ in range(dut._hs_period * 17):
            yield
            if (yield dut.new_frame):
                new_frames += 1
        self.assertEqual(new_frames, 2)


# ── CRC5 ────────────────────────────────────────────────────────────────────

class USBCRC5Test(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = USBCRC5
    FRAGMENT_ARGUMENTS  = {"width": 11}

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    @usb_domain_test_case
    def test_crc5_all_zeros(self):
        """ CRC5 of all-zeros: 0x02 (witness: SETUP token 2D 00 10). """
        dut = self.dut
        yield dut.data.eq(0x000)
        yield
        self.assertEqual((yield dut.crc), 0x02)

    @usb_domain_test_case
    def test_crc5_all_ones(self):
        """ CRC5 of all-ones (0x7FF) = 0x08 per reference model. """
        dut = self.dut
        yield dut.data.eq(0x7FF)
        yield
        self.assertEqual((yield dut.crc), crc5_reference(0x7FF))
        self.assertEqual((yield dut.crc), 0x08)

    @usb_domain_test_case
    def test_crc5_exhaustive_sample(self):
        """ Gateware CRC5 matches the reference model across the input space. """
        dut = self.dut
        for value in range(0, 2048, 13):
            yield dut.data.eq(value)
            yield
            got = (yield dut.crc)
            self.assertEqual(got, crc5_reference(value),
                f"CRC5 mismatch for {value:#05x}: got {got:#04x}")

    @usb_domain_test_case
    def test_crc5_boundaries(self):
        """ Boundary values 0, 1, 0x400, 0x7FE, 0x7FF all match. """
        dut = self.dut
        for value in (0, 1, 0x400, 0x7FE, 0x7FF, 0x555, 0x2AA):
            yield dut.data.eq(value)
            yield
            self.assertEqual((yield dut.crc), crc5_reference(value))


# ── Token generator ─────────────────────────────────────────────────────────

class USBHostTokenGeneratorTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = USBHostTokenGenerator

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def instantiate_dut(self):
        utmi = type('UTMIStub', (), {})()
        utmi.tx_data  = Signal(8)
        utmi.tx_valid = Signal()
        utmi.tx_ready = Signal()
        dut = USBHostTokenGenerator(utmi=utmi, domain_clock=60e6)
        # Squash the SOF counter so tests run fast
        dut.sof_counter._hs_period = 5
        dut.sof_counter._fs_period = 8
        return dut

    def issue_token(self, pid, address, endpoint):
        """ Issue one token and capture the transmitted bytes. """
        dut = self.dut
        yield dut.sof_enable.eq(0)
        yield dut.tx_ready.eq(1)
        yield dut.token_pid.eq(pid)
        yield dut.token_address.eq(address)
        yield dut.token_endpoint.eq(endpoint)
        yield dut.issue_token.eq(1)
        yield
        yield dut.issue_token.eq(0)

        bytes_out = []
        for _ in range(20):
            yield
            if (yield dut.tx_valid):
                bytes_out.append((yield dut.tx_data))
            if len(bytes_out) >= 3:
                break
        return bytes_out

    @usb_domain_test_case
    def test_setup_token_exact(self):
        """ SETUP addr=0 ep=0 → 2D 00 10 (known wire capture). """
        bytes_out = yield from self.issue_token(USBPacketID.SETUP, 0, 0)
        self.assertEqual(bytes_out, [0x2D, 0x00, 0x10])

    @usb_domain_test_case
    def test_in_token_exact(self):
        """ IN addr=1 ep=0 → 69 01 E8. """
        bytes_out = yield from self.issue_token(USBPacketID.IN, 1, 0)
        self.assertEqual(bytes_out, [0x69, 0x01, 0xE8])

    @usb_domain_test_case
    def test_out_token_exact(self):
        """ OUT addr=0x2A ep=3 matches the reference model. """
        bytes_out = yield from self.issue_token(USBPacketID.OUT, 0x2A, 3)
        payload = (3 << 7) | 0x2A
        self.assertEqual(bytes_out, token_bytes_reference(USBPacketID.OUT, payload))

    @usb_domain_test_case
    def test_ping_token_exact(self):
        """ PING addr=5 ep=1 matches the reference model. """
        bytes_out = yield from self.issue_token(USBPacketID.PING, 5, 1)
        payload = (1 << 7) | 5
        self.assertEqual(bytes_out, token_bytes_reference(USBPacketID.PING, payload))

    @usb_domain_test_case
    def test_token_busy_during_transmit(self):
        """ token_busy is high while a token is being sent. """
        dut = self.dut
        yield dut.sof_enable.eq(0)
        yield dut.tx_ready.eq(1)
        yield dut.token_pid.eq(USBPacketID.IN)
        yield dut.issue_token.eq(1)
        yield
        yield dut.issue_token.eq(0)
        yield
        self.assertEqual((yield dut.token_busy), 1)
        # let it finish
        for _ in range(10):
            yield
        self.assertEqual((yield dut.token_busy), 0)

    @usb_domain_test_case
    def test_sof_token_output(self):
        """ SOF token frame 0 → A5 00 10. """
        dut = self.dut
        yield dut.tx_ready.eq(1)
        yield dut.sof_enable.eq(1)
        yield dut.sof_counter.speed.eq(0)  # HS

        bytes_out = []
        for _ in range(30):
            yield
            if (yield dut.tx_valid):
                bytes_out.append((yield dut.tx_data))
            if len(bytes_out) >= 3:
                break

        self.assertEqual(bytes_out, [0xA5, 0x00, 0x10])
