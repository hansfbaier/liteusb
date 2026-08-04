#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" USB host transfer execution engine.

Issues tokens, sends/receives data packets, processes handshakes,
handles timeouts and retries.  Works in concert with the EHCI schedule
processor to execute individual USB transfers.

The packet-layer components (data packet generator/receiver, handshake
detector/generator) are shared with the rest of the host controller and
are passed in from the parent module; this engine only orchestrates them.

Semantics follow EHCI Rev 1.0 §4.10 and the qTD status model (§3.5.3):
  - ACK            → transfer complete
  - NAK            → transaction retires WITHOUT consuming an error retry;
                     the qTD stays active and is retried on a later pass
  - STALL          → endpoint halt
  - NYET (HS)      → complete with NYET (ping protocol follows)
  - timeout / CRC  → transaction error; consumes one CERR retry
  - babble         → more bytes than MaxPacket; treated as an error
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBPacketID, USBSpeed
from ..packet import USBInStreamInterface, USBOutStreamInterface


# ── Transfer Request Interface ──────────────────────────────────────────────

class HostTransferRequest(Record):
    """Record carrying a transfer request from the schedule engine."""

    def __init__(self):
        super().__init__([
            ("valid",         1),   # level: start transfer
            ("pid",           4),   # IN / OUT / SETUP / PING
            ("address",       7),   # device address
            ("endpoint",      4),   # endpoint number
            ("data_toggle",   1),   # DATA0 or DATA1
            ("length",       16),   # total bytes to transfer (0 = ZLP)
            ("max_packet",   10),   # max packet size (babble threshold)
            ("speed",         2),   # device speed (for timeouts)
            ("cerr",          2),   # error counter reload (retries)
            ("ioc",           1),   # interrupt on complete
        ])


# ── Transfer Response Interface ─────────────────────────────────────────────

class HostTransferResponse(Record):
    """Record carrying a transfer completion back to the schedule engine."""

    def __init__(self):
        super().__init__([
            ("done",          1),   # pulse: transfer complete
            ("ack",           1),   # device ACKed
            ("nak",           1),   # device NAKed (no error consumed)
            ("stall",         1),   # device STALLed
            ("nyet",          1),   # device NYETed (HS only)
            ("error",         1),   # retries exhausted (timeout/CRC/babble)
            ("babble",        1),   # device sent too much data
            ("bytes_xfer",   11),   # bytes actually transferred
            ("data_toggle",   1),   # data toggle after the transfer
        ])


# Response timeout in UTMI clock cycles, per speed.
# (USB 2.0 §7.1.19.1: a device must respond within 6.5 bit times at FS,
# 192 bit times at HS for non-periodic; we allow generous margins.)
_TIMEOUT_HS = 92     # ~1.5 µs at 60 MHz
_TIMEOUT_FS = 960    # ~16 µs  at 60 MHz
_TIMEOUT_LS = 7680   # ~128 µs at 60 MHz


class _TimeoutConfig:
    """ Instance-copyable timeout configuration (tests may squash these
        before elaboration to keep simulations short). """
    def __init__(self):
        self.hs = _TIMEOUT_HS
        self.fs = _TIMEOUT_FS
        self.ls = _TIMEOUT_LS


# ── Host Transfer Engine ────────────────────────────────────────────────────

class USBHostTransferEngine(Module):
    """ Executes a single USB host transfer from token to handshake.

    Parameters
    ----------
    utmi : UTMIInterface
        UTMI bus (for line timing references).
    data_tx : USBDataPacketGenerator
        Shared data packet generator (OUT/SETUP data phase).
    data_rx : USBDataPacketReceiver
        Shared data packet receiver (IN data phase).
    hs_detect : USBHandshakeDetector
        Shared handshake detector (ACK/NAK/STALL/NYET from device).
    hs_gen : USBHandshakeGenerator
        Shared handshake generator (ACK to device after IN data).

    Interface
    ---------
    request : HostTransferRequest
    response : HostTransferResponse
    tx_stream : USBInStreamInterface
        Payload bytes for OUT/SETUP data phases (length bytes; for a
        zero-length packet, pulse valid+last with no first).
    rx_stream : USBOutStreamInterface
        Payload bytes received during IN data phases.
    token_* : external token-generator wiring (driven by the parent).
    bus_granted : Signal() input
        High while the engine may use the bus.
    """

    def __init__(self, utmi, data_tx, data_rx, hs_detect, hs_gen):
        self.utmi      = utmi
        self.data_tx   = data_tx
        self.data_rx   = data_rx
        self.hs_detect = hs_detect
        self.hs_gen    = hs_gen

        # Request from schedule engine
        self.request = HostTransferRequest()

        # Response to schedule engine
        self.response = HostTransferResponse()

        # Payload streams
        self.tx_stream = USBInStreamInterface()
        self.rx_stream = USBOutStreamInterface()

        # Signal that we've claimed the bus
        self.bus_granted = Signal()

        # Timeout configuration (squashable pre-elaboration, for tests)
        self.timeouts = _TimeoutConfig()

        # ── Token generator interface (external — wired by the parent) ──
        # These must be module attributes created in __init__ so the
        # parent module's do_finalize() can reference them before this
        # module's own do_finalize() runs.
        self.token_pid      = Signal(4)
        self.token_address  = Signal(7)
        self.token_endpoint = Signal(4)
        self.token_issue    = Signal()
        self.token_busy     = Signal()

    def do_finalize(self):
        data_tx   = self.data_tx
        data_rx   = self.data_rx
        hs_det    = self.hs_detect
        hs_gen    = self.hs_gen

        # ── Transfer state ──────────────────────────────────────────────

        active_pid    = Signal(4)
        active_addr   = Signal(7)
        active_ep     = Signal(4)
        data_toggle   = Signal()
        length        = Signal(16)
        err_count     = Signal(3)
        max_err       = Signal(3)
        max_packet    = Signal(10)
        speed         = Signal(2)

        tx_count      = Signal(16)
        rx_count      = Signal(16)

        timeout_counter = Signal(16)
        timeout_limit   = Signal(16)

        self.comb += [
            If(speed == USBSpeed.HIGH,
                timeout_limit.eq(self.timeouts.hs),
            ).Elif(speed == USBSpeed.FULL,
                timeout_limit.eq(self.timeouts.fs),
            ).Else(
                timeout_limit.eq(self.timeouts.ls),
            )
        ]

        # FSM (USB clock domain)
        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.xfer_fsm = fsm

        # Timeout counting in states that wait for a device response
        self.sync.usb += [
            If(fsm.ongoing("WAIT_DATA") | fsm.ongoing("WAIT_HANDSHAKE") |
               fsm.ongoing("WAIT_TX_DONE"),
                If(timeout_counter != 0,
                    timeout_counter.eq(timeout_counter - 1),
                ),
            )
        ]

        # ── Response defaults: cleared whenever we are idle ─────────────
        self.sync.usb += [
            If(fsm.ongoing("IDLE"),
                self.response.done.eq(0),
                self.response.ack.eq(0),
                self.response.nak.eq(0),
                self.response.stall.eq(0),
                self.response.nyet.eq(0),
                self.response.error.eq(0),
                self.response.babble.eq(0),
            )
        ]

        # ── IDLE: wait for transfer request ─────────────────────────────

        # Drive defaults (overridden by FSM states below)
        self.comb += [
            self.token_pid.eq(active_pid),
            self.token_address.eq(active_addr),
            self.token_endpoint.eq(active_ep),
            self.token_issue.eq(0),
            hs_gen.issue_ack.eq(0),
        ]

        fsm.act("IDLE",
            If(self.request.valid & self.bus_granted,
                NextValue(active_pid,  self.request.pid),
                NextValue(active_addr, self.request.address),
                NextValue(active_ep,   self.request.endpoint),
                NextValue(data_toggle, self.request.data_toggle),
                NextValue(length,      self.request.length),
                NextValue(max_err,     self.request.cerr),
                NextValue(err_count,   0),
                NextValue(max_packet,  self.request.max_packet),
                NextValue(speed,       self.request.speed),
                NextValue(tx_count,    0),
                NextValue(rx_count,    0),
                NextState("ISSUE_TOKEN"),
            )
        )

        # ── ISSUE_TOKEN: request the token from the token generator ─────

        fsm.act("ISSUE_TOKEN",
            self.token_issue.eq(1),
            If(self.token_busy,
                # Token generator has started transmitting
                NextState("TOKEN_WAIT"),
            )
        )

        # ── TOKEN_WAIT: token transmission in progress ──────────────────

        fsm.act("TOKEN_WAIT",
            If(~self.token_busy,
                If(active_pid == USBPacketID.IN,
                    NextValue(timeout_counter, self.timeouts.ls),
                    NextState("WAIT_DATA"),
                ).Elif((active_pid == USBPacketID.OUT) |
                       (active_pid == USBPacketID.SETUP),
                    NextState("SEND_DATA"),
                ).Elif(active_pid == USBPacketID.PING,
                    NextValue(timeout_counter, self.timeouts.ls),
                    NextState("WAIT_HANDSHAKE"),
                ).Else(
                    NextState("COMPLETE"),
                )
            )
        )

        # ── SEND_DATA (OUT/SETUP): stream the data packet out ───────────
        #
        # Bridge our tx_stream into the shared packet generator, marking
        # the first and last bytes so it can emit PID/payload/CRC.

        first_byte = (tx_count == 0)
        last_byte  = (tx_count == (length - 1))

        self.comb += [
            If(fsm.ongoing("SEND_DATA") & (length != 0),
                data_tx.stream.payload.eq(self.tx_stream.payload),
                data_tx.stream.valid.eq(self.tx_stream.valid),
                data_tx.stream.first.eq(first_byte),
                data_tx.stream.last.eq(last_byte),
                self.tx_stream.ready.eq(data_tx.stream.ready),
            ).Elif(fsm.ongoing("SEND_DATA"),
                # Zero-length packet: pulse valid+last without first
                data_tx.stream.valid.eq(1),
                data_tx.stream.last.eq(1),
                data_tx.stream.first.eq(0),
            ),
            data_tx.data_pid.eq(data_toggle),
        ]

        self.sync.usb += [
            If(fsm.ongoing("SEND_DATA") & (length != 0) &
               self.tx_stream.valid & data_tx.stream.ready,
                tx_count.eq(tx_count + 1),
            )
        ]

        fsm.act("SEND_DATA",
            If(length == 0,
                # ZLP triggered; wait for the packet to drain
                NextValue(timeout_counter, self.timeouts.ls),
                NextState("WAIT_TX_DONE"),
            ).Elif(self.tx_stream.valid & data_tx.stream.ready & last_byte,
                NextValue(timeout_counter, self.timeouts.ls),
                NextState("WAIT_TX_DONE"),
            )
        )

        # ── WAIT_TX_DONE: packet generator is emitting PID+payload+CRC ──

        fsm.act("WAIT_TX_DONE",
            If(~data_tx.tx.valid,
                # Transmission finished; await the device handshake
                NextValue(timeout_counter, self.timeouts.ls),
                NextState("WAIT_HANDSHAKE"),
            ).Elif(timeout_counter == 0,
                NextState("RETRY_CHECK"),
            )
        )

        # ── WAIT_DATA (IN): receive the data packet ─────────────────────
        #
        # Bridge the shared receiver's stream to our rx_stream and count
        # bytes so the schedule engine knows how much arrived.
        # (USBOutStreamInterface: valid frames the packet, next strobes
        # each valid byte; there is no ready — we must keep up.)

        self.comb += [
            If(fsm.ongoing("WAIT_DATA"),
                self.rx_stream.payload.eq(data_rx.stream.payload),
                self.rx_stream.valid.eq(data_rx.stream.valid),
                self.rx_stream.next.eq(data_rx.stream.next),
            )
        ]

        self.sync.usb += [
            If(fsm.ongoing("WAIT_DATA") &
               data_rx.stream.valid & data_rx.stream.next,
                rx_count.eq(rx_count + 1),
            )
        ]

        fsm.act("WAIT_DATA",
            If(data_rx.crc_mismatch,
                # CRC error: treat as a transaction error (retry)
                NextState("RETRY_CHECK"),
            ).Elif(data_rx.packet_complete,
                If(rx_count > max_packet,
                    NextState("COMPLETE_BABBLE"),
                ).Else(
                    NextState("SEND_HANDSHAKE"),
                )
            ).Elif(timeout_counter == 0,
                NextState("RETRY_CHECK"),
            )
        )

        # ── SEND_HANDSHAKE (IN): ACK the received data ──────────────────

        fsm.act("SEND_HANDSHAKE",
            hs_gen.issue_ack.eq(1),
            NextState("COMPLETE"),
        )

        # ── WAIT_HANDSHAKE (OUT/SETUP/PING): receive the handshake ──────

        fsm.act("WAIT_HANDSHAKE",
            If(hs_det.detected.ack,
                NextState("COMPLETE"),
            ).Elif(hs_det.detected.nak,
                # NAK retires the transaction without consuming a retry
                NextState("COMPLETE_NAK"),
            ).Elif(hs_det.detected.stall,
                NextState("COMPLETE_STALL"),
            ).Elif(hs_det.detected.nyet,
                NextState("COMPLETE_NYET"),
            ).Elif(timeout_counter == 0,
                NextState("RETRY_CHECK"),
            )
        )

        # ── RETRY_CHECK: consume an error retry or fail ─────────────────

        fsm.act("RETRY_CHECK",
            If(err_count < max_err,
                NextValue(err_count, err_count + 1),
                NextValue(tx_count, 0),
                NextValue(rx_count, 0),
                NextState("ISSUE_TOKEN"),
            ).Else(
                NextState("COMPLETE_ERROR"),
            )
        )

        # ── COMPLETE variants (single-cycle response pulses) ────────────

        fsm.act("COMPLETE",
            NextValue(self.response.done, 1),
            NextValue(self.response.ack, 1),
            NextValue(self.response.bytes_xfer, rx_count),
            NextValue(self.response.data_toggle, data_toggle ^ 1),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_NAK",
            NextValue(self.response.done, 1),
            NextValue(self.response.nak, 1),
            NextValue(self.response.bytes_xfer, 0),
            NextValue(self.response.data_toggle, data_toggle),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_STALL",
            NextValue(self.response.done, 1),
            NextValue(self.response.stall, 1),
            NextValue(self.response.data_toggle, data_toggle),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_NYET",
            NextValue(self.response.done, 1),
            NextValue(self.response.nyet, 1),
            NextValue(self.response.data_toggle, data_toggle),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_BABBLE",
            NextValue(self.response.done, 1),
            NextValue(self.response.error, 1),
            NextValue(self.response.babble, 1),
            NextValue(self.response.bytes_xfer, rx_count),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_ERROR",
            NextValue(self.response.done, 1),
            NextValue(self.response.error, 1),
            NextValue(self.response.bytes_xfer, rx_count),
            NextValue(self.response.data_toggle, data_toggle),
            NextState("IDLE"),
        )
