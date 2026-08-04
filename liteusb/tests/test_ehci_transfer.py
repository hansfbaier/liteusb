#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI transfer engine tests — full transaction flows through real
    packet-layer components.

Tests cover (EHCI Rev 1.0 §4.10, USB 2.0 §8.5):
  - OUT/SETUP: token issue → data packet (PID + payload + CRC16) → handshake
  - IN: token issue → data receive → CRC check → ACK handshake → bytes_xfer
  - ACK completes, toggles data toggle
  - NAK retires the transaction WITHOUT consuming an error retry
  - STALL completes with stall status
  - Handshake timeout retries CERR times, then errors
  - Zero-length packets
"""

from migen import Signal, Module

from liteusb.tests.test_case import LiteUSBUSBTestCase, usb_domain_test_case
from liteusb.gateware.usb.usb2        import USBPacketID, USBSpeed
from liteusb.gateware.usb.usb2.packet import (
    USBDataPacketGenerator, USBDataPacketReceiver,
    USBHandshakeDetector, USBHandshakeGenerator, USBDataPacketCRC,
)
from liteusb.gateware.usb.usb2.host.transfer import (
    HostTransferRequest, HostTransferResponse, USBHostTransferEngine,
)


def _crc16_next_byte(crc, d):
    """ Bit-exact replica of USBDataPacketCRC._generate_next_crc. """
    xorb = lambda bits: __import__('functools').reduce(int.__xor__, bits, 0)
    b = [(d >> i) & 1 for i in range(8)]
    c = [(crc >> i) & 1 for i in range(16)]
    out = [0] * 16
    out[0]  = xorb(b)       ^ xorb(c[8:16])
    out[1]  = xorb(b[0:7])  ^ xorb(c[9:16])
    out[2]  = xorb(b[6:8])  ^ xorb(c[8:10])
    out[3]  = xorb(b[5:7])  ^ xorb(c[9:11])
    out[4]  = xorb(b[4:6])  ^ xorb(c[10:12])
    out[5]  = xorb(b[3:5])  ^ xorb(c[11:13])
    out[6]  = xorb(b[2:4])  ^ xorb(c[12:14])
    out[7]  = xorb(b[1:3])  ^ xorb(c[13:15])
    out[8]  = xorb(b[0:2])  ^ xorb(c[14:16]) ^ c[0]
    out[9]  = b[0] ^ c[1] ^ c[15]
    out[10] = c[2]
    out[11] = c[3]
    out[12] = c[4]
    out[13] = c[5]
    out[14] = c[6]
    out[15] = xorb(b)       ^ xorb(c[7:16])
    return sum(bit << i for i, bit in enumerate(out))


def crc16_usb_bytes(data):
    """ CRC-16 bytes for a USB data packet, as transmitted on the wire
        (matching the gateware USBDataPacketCRC output, low byte first). """
    crc = 0xFFFF
    for byte in data:
        crc = _crc16_next_byte(crc, byte)
    # gateware output: complement of the bit-reversed running CRC
    rev = int('{:016b}'.format(crc)[::-1], 2)
    out = (~rev) & 0xFFFF
    return [out & 0xFF, (out >> 8) & 0xFF]


class _UTMIStub:
    """ Minimal UTMI stub with the signals the packet layer needs. """
    def __init__(self):
        self.rx_data    = Signal(8)
        self.rx_valid   = Signal()
        self.rx_active  = Signal()
        self.tx_data    = Signal(8)
        self.tx_valid   = Signal()
        self.tx_ready   = Signal()
        self.line_state = Signal(2)


class _XferHarness(Module):
    """ Transfer engine + shared packet components, wired like ehci.py.

    Clock domains are provided by the test infrastructure.
    """

    def __init__(self):
        utmi = _UTMIStub()
        self.utmi = utmi

        self.submodules.data_crc = data_crc = USBDataPacketCRC()
        self.submodules.data_tx  = data_tx  = USBDataPacketGenerator()
        data_crc.add_interface(data_tx.crc)
        self.submodules.data_rx  = data_rx  = USBDataPacketReceiver(utmi=utmi)
        data_crc.add_interface(data_rx.data_crc)
        self.submodules.hs_det   = hs_det   = USBHandshakeDetector(utmi=utmi)
        self.submodules.hs_gen   = hs_gen   = USBHandshakeGenerator()

        self.submodules.xfer = xfer = USBHostTransferEngine(
            utmi=utmi, data_tx=data_tx, data_rx=data_rx,
            hs_detect=hs_det, hs_gen=hs_gen)

        # CRC tap on the UTMI receive path (as in ehci.py)
        self.comb += [
            data_crc.rx_data.eq(utmi.rx_data),
            data_crc.rx_valid.eq(utmi.rx_valid),
            data_crc.tx_valid.eq(data_tx.tx.valid & utmi.tx_ready),
            data_crc.tx_data.eq(data_tx.tx.data),
        ]


class USBHostTransferEngineTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = _XferHarness

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def instantiate_dut(self):
        dut = super().instantiate_dut()
        # Squash response timeouts for simulation speed
        dut.xfer.timeouts.hs = 20
        dut.xfer.timeouts.fs = 20
        dut.xfer.timeouts.ls = 20
        return dut

    #
    # Helpers
    #
    def start_request(self, pid, address=1, endpoint=0, length=0,
                      max_packet=64, toggle=0, cerr=3):
        dut = self.dut.xfer
        yield dut.request.pid.eq(pid)
        yield dut.request.address.eq(address)
        yield dut.request.endpoint.eq(endpoint)
        yield dut.request.length.eq(length)
        yield dut.request.max_packet.eq(max_packet)
        yield dut.request.data_toggle.eq(toggle)
        yield dut.request.cerr.eq(cerr)
        yield dut.bus_granted.eq(1)
        yield dut.request.valid.eq(1)
        yield
        yield dut.request.valid.eq(0)

    def pass_token(self):
        """ Emulate the token generator: busy for a few cycles. """
        dut = self.dut.xfer
        # wait for token_issue
        for _ in range(10):
            if (yield dut.token_issue):
                break
            yield
        self.assertEqual((yield dut.token_issue), 1)
        yield dut.token_busy.eq(1)
        for _ in range(3):
            yield
        yield dut.token_busy.eq(0)
        yield

    def device_handshake(self, pid_byte):
        """ Emulate a handshake packet from the device on the UTMI bus.
            (rx_active leads the first byte, as a real PHY does.) """
        utmi = self.dut.utmi
        yield utmi.rx_active.eq(1)
        yield
        yield utmi.rx_data.eq(pid_byte)
        yield utmi.rx_valid.eq(1)
        yield
        yield utmi.rx_valid.eq(0)
        yield
        yield utmi.rx_active.eq(0)
        yield

    def device_data_packet(self, pid_byte, payload):
        """ Emulate a data packet (PID + payload + CRC16) from the device.
            (rx_active leads the first byte, as a real PHY does.) """
        utmi = self.dut.utmi
        crc = crc16_usb_bytes(payload)
        yield utmi.rx_active.eq(1)
        yield
        for byte in [pid_byte] + list(payload) + crc:
            yield utmi.rx_data.eq(byte)
            yield utmi.rx_valid.eq(1)
            yield
        yield utmi.rx_valid.eq(0)
        yield
        yield utmi.rx_active.eq(0)
        yield

    def wait_done(self, cycles=200):
        dut = self.dut.xfer
        for _ in range(cycles):
            yield
            if (yield dut.response.done):
                return True
        return False

    def capture_tx_bytes(self, cycles):
        """ Capture bytes the data packet generator transmits. """
        dut = self.dut
        bytes_out = []
        for _ in range(cycles):
            yield self.dut.data_tx.tx.ready.eq(1)
            yield
            if (yield dut.data_tx.tx.valid):
                bytes_out.append((yield dut.data_tx.tx.data))
        return bytes_out

    #
    # Tests
    #
    @usb_domain_test_case
    def test_idle_defaults(self):
        """ Engine starts idle with a quiet response record. """
        resp = self.dut.xfer.response
        self.assertEqual((yield resp.done), 0)
        self.assertEqual((yield resp.ack), 0)
        self.assertEqual((yield resp.nak), 0)
        self.assertEqual((yield resp.stall), 0)
        self.assertEqual((yield resp.error), 0)

    @usb_domain_test_case
    def test_out_transfer_ack(self):
        """ OUT: token → DATA0 packet on the wire → ACK → done+toggle. """
        payload = [0xDE, 0xAD, 0xBE, 0xEF]

        yield from self.start_request(USBPacketID.OUT, length=len(payload),
                                      toggle=0)
        yield from self.pass_token()

        # Data phase: feed payload bytes into the engine's tx stream
        dut = self.dut.xfer
        tx_bytes = []

        # Interleave: drive tx stream, capture what data_tx emits;
        # stop as soon as the packet has fully drained
        sent = 0
        for cycle in range(120):
            if sent < len(payload):
                yield dut.tx_stream.payload.eq(payload[sent])
                yield dut.tx_stream.valid.eq(1)
            else:
                yield dut.tx_stream.valid.eq(0)
            yield self.dut.data_tx.tx.ready.eq(1)
            yield
            if (yield self.dut.data_tx.tx.valid):
                tx_bytes.append((yield self.dut.data_tx.tx.data))
            if (yield dut.tx_stream.ready) and sent < len(payload):
                sent += 1
            if sent >= len(payload) and not (yield self.dut.data_tx.tx.valid):
                break

        yield dut.tx_stream.valid.eq(0)

        # Device ACKs the data packet
        yield from self.device_handshake(0xD2)  # ACK
        self.assertTrue((yield from self.wait_done()))

        resp = dut.response
        self.assertEqual((yield resp.ack), 1)
        self.assertEqual((yield resp.error), 0)
        # Data toggle flips after a successful transfer
        self.assertEqual((yield resp.data_toggle), 1)

        # Wire format: DATA0 PID, payload, 2 CRC bytes
        self.assertEqual(tx_bytes[0], 0xC3)          # DATA0
        self.assertEqual(tx_bytes[1:1+len(payload)], payload)

    @usb_domain_test_case
    def test_in_transfer_ack(self):
        """ IN: token → device DATA1 packet → host ACK → done, bytes_xfer. """
        payload = [0x01, 0x02, 0x03]

        yield from self.start_request(USBPacketID.IN, length=len(payload),
                                      toggle=1)
        yield from self.pass_token()

        # Device sends DATA1 with a valid CRC
        yield from self.device_data_packet(0x4B, payload)  # DATA1

        self.assertTrue((yield from self.wait_done()))

        resp = self.dut.xfer.response
        self.assertEqual((yield resp.ack), 1)
        self.assertEqual((yield resp.error), 0)
        self.assertEqual((yield resp.bytes_xfer), len(payload))
        self.assertEqual((yield resp.data_toggle), 0)  # flipped from 1

        # Host must ACK the data: handshake generator emits 0xD2
        acked = False
        yield self.dut.hs_gen.tx.ready.eq(1)
        for _ in range(10):
            yield
            if (yield self.dut.hs_gen.tx.valid):
                self.assertEqual((yield self.dut.hs_gen.tx.data), 0xD2)
                acked = True
                break
        self.assertTrue(acked)

    @usb_domain_test_case
    def test_out_nak_no_retry_consumed(self):
        """ NAK completes the transaction without error or retry (EHCI §4.10). """
        yield from self.start_request(USBPacketID.OUT, length=1, cerr=3)
        yield from self.pass_token()

        # Feed the single payload byte and wait for the packet to drain
        dut = self.dut.xfer
        sent = False
        for cycle in range(80):
            if not sent:
                yield dut.tx_stream.payload.eq(0x42)
                yield dut.tx_stream.valid.eq(1)
            else:
                yield dut.tx_stream.valid.eq(0)
            yield self.dut.data_tx.tx.ready.eq(1)
            yield
            if (yield dut.tx_stream.ready) and not sent:
                sent = True
            if sent and not (yield self.dut.data_tx.tx.valid):
                break
        yield dut.tx_stream.valid.eq(0)

        yield from self.device_handshake(0x5A)  # NAK

        self.assertTrue((yield from self.wait_done()))
        resp = dut.response
        self.assertEqual((yield resp.nak), 1)
        self.assertEqual((yield resp.error), 0)
        self.assertEqual((yield resp.ack), 0)

    @usb_domain_test_case
    def test_out_stall(self):
        """ STALL completes with stall status (EHCI §4.10: halt endpoint). """
        yield from self.start_request(USBPacketID.OUT, length=1)
        yield from self.pass_token()

        dut = self.dut.xfer
        sent = False
        for cycle in range(80):
            if not sent:
                yield dut.tx_stream.payload.eq(0x42)
                yield dut.tx_stream.valid.eq(1)
            else:
                yield dut.tx_stream.valid.eq(0)
            yield self.dut.data_tx.tx.ready.eq(1)
            yield
            if (yield dut.tx_stream.ready) and not sent:
                sent = True
            if sent and not (yield self.dut.data_tx.tx.valid):
                break
        yield dut.tx_stream.valid.eq(0)

        yield from self.device_handshake(0x1E)  # STALL

        self.assertTrue((yield from self.wait_done()))
        resp = dut.response
        self.assertEqual((yield resp.stall), 1)
        self.assertEqual((yield resp.ack), 0)

    @usb_domain_test_case
    def test_handshake_timeout_retries_then_error(self):
        """ No handshake: retry CERR times, then report error (EHCI §4.10). """
        # Timeouts squashed pre-elaboration in instantiate_dut.
        dut = self.dut.xfer
        yield from self.start_request(USBPacketID.OUT, length=0, cerr=2)

        token_issues = 0
        done = False
        for _ in range(400):
            yield
            if (yield dut.token_issue):
                token_issues += 1
                yield dut.token_busy.eq(1)
                yield
                yield
                yield dut.token_busy.eq(0)
            if (yield dut.response.done):
                done = True
                break

        self.assertTrue(done)
        self.assertEqual((yield dut.response.error), 1)
        # cerr=2 → initial attempt + 2 retries = 3 token issues
        self.assertEqual(token_issues, 3)

    @usb_domain_test_case
    def test_zlp_out(self):
        """ Zero-length OUT: data packet is PID + CRC only. """
        yield from self.start_request(USBPacketID.OUT, length=0)
        yield from self.pass_token()

        tx_bytes = yield from self.capture_tx_bytes(20)

        # Device ACKs
        yield from self.device_handshake(0xD2)

        self.assertTrue((yield from self.wait_done()))
        self.assertEqual((yield self.dut.xfer.response.ack), 1)

        # ZLP: DATA0 PID followed immediately by 2 CRC bytes
        self.assertEqual(len(tx_bytes), 3)
        self.assertEqual(tx_bytes[0], 0xC3)

    @usb_domain_test_case
    def test_setup_uses_data0(self):
        """ SETUP data phase always uses DATA0 (USB 2.0 §8.5.3). """
        payload = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00]

        yield from self.start_request(USBPacketID.SETUP, length=len(payload),
                                      toggle=0)
        yield from self.pass_token()

        dut = self.dut.xfer
        tx_bytes = []
        sent = 0
        for cycle in range(120):
            if sent < len(payload):
                yield dut.tx_stream.payload.eq(payload[sent])
                yield dut.tx_stream.valid.eq(1)
            else:
                yield dut.tx_stream.valid.eq(0)
            yield self.dut.data_tx.tx.ready.eq(1)
            yield
            if (yield self.dut.data_tx.tx.valid):
                tx_bytes.append((yield self.dut.data_tx.tx.data))
            if (yield dut.tx_stream.ready) and sent < len(payload):
                sent += 1
            if sent >= len(payload) and len(tx_bytes) >= 1 + len(payload) + 2:
                break

        self.assertEqual(tx_bytes[0], 0xC3)  # DATA0 PID
        self.assertEqual(tx_bytes[1:1+len(payload)], payload)
