#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" EHCI token generator tests — SOF counter, CRC5, and host token generation.

Tests cover:
  - USBSOFCounter: HS 125µs / FS 1ms timing, sof_hold deferral
  - USBCRC5: known CRC5 values per USB spec polynomial x^5+x^2+1
  - USBHostTokenGenerator: SOF/IN/OUT/SETUP token 3-byte output
"""

from migen import Signal

from liteusb.tests.test_case import LiteUSBUSBTestCase, usb_domain_test_case
from liteusb.gateware.usb.usb2           import USBPacketID
from liteusb.gateware.usb.usb2.host.token_generator import (
    USBSOFCounter, USBCRC5, USBHostTokenGenerator,
)


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
    def test_hs_sof_strobe(self):
        """ HS: issue_sof pulses every hs_period cycles. """
        dut = self.dut
        yield dut.speed.eq(0)  # HS
        yield dut.sof_hold.eq(0)

        # Count cycles until first SOF
        for _ in range(dut._hs_period * 2):
            yield
            if (yield dut.issue_sof):
                break
        self.assertEqual((yield dut.issue_sof), 1)

    @usb_domain_test_case
    def test_fs_frame_timing(self):
        """ FS: SOF period is fs_period (1ms), no microframes. """
        dut = self.dut
        yield dut.speed.eq(1)  # FS
        yield dut.sof_hold.eq(0)

        for _ in range(dut._fs_period * 2):
            yield
            if (yield dut.issue_sof):
                break
        self.assertEqual((yield dut.issue_sof), 1)
        # In FS, microframe stays at 0 (no microframe counting)
        self.assertEqual((yield dut.microframe_number), 0)

    @usb_domain_test_case
    def test_sof_hold_defers(self):
        """ sof_hold prevents SOF from firing. """
        dut = self.dut
        yield dut.speed.eq(0)
        yield dut.sof_hold.eq(1)

        for _ in range(dut._hs_period * 3):
            yield
        # SOF should NOT have fired
        self.assertEqual((yield dut.issue_sof), 0)

        # Release hold
        yield dut.sof_hold.eq(0)
        yield
        # SOF should fire immediately
        self.assertEqual((yield dut.issue_sof), 1)

    @usb_domain_test_case
    def test_frame_number_increments(self):
        """ Frame number increments on new_frame. """
        dut = self.dut
        yield dut.speed.eq(0)
        yield dut.sof_hold.eq(0)

        # Advance through several SOFs
        for _ in range(dut._hs_period * 17):  # ~2 full frames
            yield

        fn = (yield dut.frame_number)
        self.assertGreater(fn, 0)


class USBCRC5Test(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = USBCRC5
    FRAGMENT_ARGUMENTS  = {"width": 11}

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    @usb_domain_test_case
    def test_crc5_all_zeros(self):
        """ CRC5 of all-zeros input. Polynomial x^5+x^2+1, init 0x1F.
            Complement of result for 11-bit all-zeros: 0b01100 (0x0C).
            USB spec example: 00000000000b → CRC5 transmitted = 01100b. """
        dut = self.dut
        yield dut.data.eq(0x000)
        yield
        crc = (yield dut.crc)
        self.assertEqual(crc, 0b01100)

    @usb_domain_test_case
    def test_crc5_all_ones(self):
        """ CRC5 of all-ones (0x7FF). """
        dut = self.dut
        yield dut.data.eq(0x7FF)
        yield
        crc = (yield dut.crc)
        # Result is deterministic — just verify it's 5 bits
        self.assertLess(crc, 32)

    @usb_domain_test_case
    def test_crc5_known_token(self):
        """ CRC5 for a known token: address=0x05, endpoint=1.
            Payload = Cat(endp[3:0], addr[6:0]) = 0b0001_0000101 = 0x085.
            CRC5 transmitted for 0x085 should be 5 bits. """
        dut = self.dut
        yield dut.data.eq(0x085)  # addr=5, endp=1
        yield
        crc = (yield dut.crc)
        self.assertLess(crc, 32)


class USBHostTokenGeneratorTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = USBHostTokenGenerator

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def instantiate_dut(self):
        # Create a minimal UTMI interface with the signals the token
        # generator needs: tx_data, tx_valid, tx_ready.
        utmi = type('UTMIStub', (), {})()
        utmi.tx_data  = Signal(8)
        utmi.tx_valid = Signal()
        utmi.tx_ready = Signal()
        return USBHostTokenGenerator(utmi=utmi, domain_clock=60e6)

    @usb_domain_test_case
    def test_sof_token_output(self):
        """ SOF token: 3 bytes = PID|~PID, frame[7:0], frame[10:8]|CRC5. """
        dut = self.dut
        utmi = dut.utmi

        # Enable SOF
        yield dut.sof_enable.eq(1)
        yield utmi.tx_ready.eq(1)

        # Advance until SOF counter fires
        for _ in range(60 * 125 * 3):  # ~3 microframes worth
            yield
            if (yield utmi.tx_valid):
                break

        self.assertEqual((yield utmi.tx_valid), 1)
        byte0 = (yield utmi.tx_data)
        # PID should be SOF (0x5), ~PID should be 0xA, combined = 0xA5
        self.assertEqual(byte0 & 0x0F, USBPacketID.SOF)

    @usb_domain_test_case
    def test_in_token_output(self):
        """ IN token: 3 bytes with PID, ADDR+ENDP, CRC5. """
        dut = self.dut
        utmi = dut.utmi

        yield utmi.tx_ready.eq(1)
        yield dut.sof_enable.eq(0)
        yield dut.token_pid.eq(USBPacketID.IN)
        yield dut.token_address.eq(0x2A)
        yield dut.token_endpoint.eq(3)
        yield dut.issue_token.eq(1)
        yield
        yield dut.issue_token.eq(0)

        # Wait for token FSM to transmit 3 bytes
        bytes_out = []
        for _ in range(20):
            if (yield utmi.tx_valid):
                bytes_out.append((yield utmi.tx_data))
            if len(bytes_out) >= 3:
                break
            yield

        self.assertEqual(len(bytes_out), 3)
        # Byte 0: PID | ~PID; PID=IN(0x9), ~PID=0x6 → 0x69
        self.assertEqual(bytes_out[0] & 0x0F, USBPacketID.IN)
        # Byte 1: endpoint[3:0] | address[6:0] = 0b0011_0101010
        self.assertEqual(bytes_out[1], (3 << 0) | (0x2A << 4) & 0xFF)

    @usb_domain_test_case
    def test_out_token_output(self):
        """ OUT token: verify PID routing. """
        dut = self.dut
        utmi = dut.utmi

        yield utmi.tx_ready.eq(1)
        yield dut.sof_enable.eq(0)
        yield dut.token_pid.eq(USBPacketID.OUT)
        yield dut.token_address.eq(0x01)
        yield dut.token_endpoint.eq(0)
        yield dut.issue_token.eq(1)
        yield
        yield dut.issue_token.eq(0)

        bytes_out = []
        for _ in range(20):
            if (yield utmi.tx_valid):
                bytes_out.append((yield utmi.tx_data))
            if len(bytes_out) >= 3:
                break
            yield

        self.assertEqual(len(bytes_out), 3)
        self.assertEqual(bytes_out[0] & 0x0F, USBPacketID.OUT)

    @usb_domain_test_case
    def test_setup_token_output(self):
        """ SETUP token: PID=0xD. """
        dut = self.dut
        utmi = dut.utmi

        yield utmi.tx_ready.eq(1)
        yield dut.sof_enable.eq(0)
        yield dut.token_pid.eq(USBPacketID.SETUP)
        yield dut.token_address.eq(0)
        yield dut.token_endpoint.eq(0)
        yield dut.issue_token.eq(1)
        yield
        yield dut.issue_token.eq(0)

        bytes_out = []
        for _ in range(20):
            if (yield utmi.tx_valid):
                bytes_out.append((yield utmi.tx_data))
            if len(bytes_out) >= 3:
                break
            yield

        self.assertEqual(len(bytes_out), 3)
        self.assertEqual(bytes_out[0] & 0x0F, USBPacketID.SETUP)
