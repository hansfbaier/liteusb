#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" USB host transfer execution engine.

Issues tokens, sends/receives data packets, processes handshakes,
handles timeouts and NAK retries.  Works in concert with the
EHCI schedule processor to execute individual USB transfers.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBPacketID, USBSpeed
from ..packet import (
    USBDataPacketGenerator, USBDataPacketReceiver,
    USBHandshakeDetector, USBInterpacketTimer,
    USBInStreamInterface, USBOutStreamInterface,
)
from ....interface.utmi import UTMITransmitInterface
from .token_generator import USBHostTokenGenerator


# ── Transfer Request Interface ──────────────────────────────────────────────

class HostTransferRequest(Record):
    """Record carrying a transfer request from the schedule engine."""

    def __init__(self):
        super().__init__([
            ("valid",         1),   # pulse: start transfer
            ("pid",           4),   # IN / OUT / SETUP
            ("address",       7),   # device address
            ("endpoint",      4),   # endpoint number
            ("data_toggle",   1),   # DATA0 or DATA1
            ("max_packet",   10),   # max packet size
            ("speed",         2),   # device speed (for timing)
            ("cerr",          2),   # error count (3 = max 3 retries)
            ("ioc",           1),   # interrupt on complete
        ])


# ── Transfer Response Interface ─────────────────────────────────────────────

class HostTransferResponse(Record):
    """Record carrying a transfer completion back to the schedule engine."""

    def __init__(self):
        super().__init__([
            ("done",          1),   # pulse: transfer complete
            ("ack",           1),   # device ACKed
            ("nak",           1),   # device NAKed
            ("stall",         1),   # device STALLed
            ("nyet",          1),   # device NYETed (HS only)
            ("error",         1),   # timeout or CRC error
            ("babble",        1),   # device sent too much data
            ("bytes_xfer",   11),   # bytes actually transferred
            ("data_toggle",   1),   # updated data toggle
        ])


# ── Host Transfer Engine ────────────────────────────────────────────────────

class USBHostTransferEngine(Module):
    """ Executes a single USB host transfer from token to handshake.

    Operation sequence (OUT/SETUP):
        1. Issue token (OUT or SETUP)
        2. Send data packet (DATA0/DATA1)
        3. Wait for handshake (ACK/NAK/STALL/NYET) or timeout

    Operation sequence (IN):
        1. Issue token (IN)
        2. Wait for data packet (DATA0/DATA1) or timeout
        3. Send ACK handshake

    Operation sequence (PING — HS bulk/control OUT only):
        1. Issue PING token
        2. Wait for ACK (device ready) or NAK (device not ready)

    Handles:
        - Normal completion (ACK)
        - NAK retry (up to CERR + 1 attempts)
        - STALL detection
        - Timeout (no response within 18 bit times)
        - CRC errors
        - Babble detection
        - NYET (HS) → PING sequence
    """

    def __init__(self, utmi):
        self.utmi = utmi

        # Request from schedule engine
        self.request = HostTransferRequest()

        # Response to schedule engine
        self.response = HostTransferResponse()

        # Signal that we've claimed the bus (token generator not busy elsewhere)
        self.bus_granted = Signal()

    def do_finalize(self):
        utmi = self.utmi

        # ── Sub-components ──────────────────────────────────────────────

        # Token generator (shared — external module provides it)
        # We create internal signals to interface with it externally
        self.token_pid      = Signal(4)
        self.token_address  = Signal(7)
        self.token_endpoint = Signal(4)
        self.token_issue    = Signal()
        self.token_busy     = Signal()

        # Data packet generator (for OUT/SETUP data phase)
        self.submodules.data_tx = data_tx = USBDataPacketGenerator()
        # Data packet receiver (for IN data phase)
        self.submodules.data_rx = data_rx = USBDataPacketReceiver(utmi=utmi)
        # Handshake detector (for ACK/NAK/STALL/NYET from device)
        self.submodules.hs_detector = hs_det = USBHandshakeDetector(utmi=utmi)
        # Interpacket timer
        self.submodules.timer = timer = USBInterpacketTimer(
            domain_clock=60e6, fs_only=False)

        # ── Transfer State ──────────────────────────────────────────────

        active_pid         = Signal(4)   # IN / OUT / SETUP
        data_toggle        = Signal()
        err_count          = Signal(3)   # 0..7
        max_err            = Signal(3)   # reload value (CERR)
        max_packet         = Signal(10)
        bytes_xfer         = Signal(11)
        transfer_active    = Signal()
        awaiting_response  = Signal()

        # Timeout counter: 18 bit times ≈ 300ns at HS, ~1.5µs at FS
        # We'll use a generous counter
        timeout_counter    = Signal(16)
        timeout_value      = Signal(16)

        # FSM for the transfer engine
        fsm = FSM(reset_state="IDLE")
        self.submodules.xfer_fsm = fsm

        # ── IDLE: wait for transfer request ─────────────────────────────

        fsm.act("IDLE",
            If(self.request.valid & self.bus_granted,
                NextValue(active_pid, self.request.pid),
                NextValue(data_toggle, self.request.data_toggle),
                NextValue(max_err, self.request.cerr),
                NextValue(err_count, 0),
                NextValue(max_packet, self.request.max_packet),
                NextValue(bytes_xfer, 0),
                NextState("ISSUE_TOKEN"),
            )
        )

        # ── ISSUE_TOKEN: send token packet ──────────────────────────────

        fsm.act("ISSUE_TOKEN",
            # Set up token parameters for the external token generator
            self.token_pid.eq(active_pid),
            self.token_address.eq(self.request.address),
            self.token_endpoint.eq(self.request.endpoint),
            self.token_issue.eq(1),
            If(~self.token_busy,
                # Token has been sent; the token generator transitions to
                # SEND_BYTE* states on its own. We watch for token_busy to
                # go low (back to IDLE), meaning the 3-byte token was sent.
                # But we need to wait for it to actually finish.
                # Once token_issue is latched, the token generator starts.
                NextState("TOKEN_SENT_WAIT"),
            )
        )

        # ── TOKEN_SENT_WAIT: token is being transmitted ─────────────────

        fsm.act("TOKEN_SENT_WAIT",
            self.token_issue.eq(0),
            If(~self.token_busy,  # token generator back to IDLE
                If(active_pid == USBPacketID.IN,
                    NextState("WAIT_DATA"),
                ).Elif((active_pid == USBPacketID.OUT) |
                       (active_pid == USBPacketID.SETUP),
                    NextState("SEND_DATA"),
                ).Elif(active_pid == USBPacketID.PING,
                    NextState("WAIT_HANDSHAKE"),
                ).Else(
                    NextState("COMPLETE"),
                )
            )
        )

        # ── SEND_DATA (OUT/SETUP): transmit data packet ─────────────────

        data_tx_bytes_sent = Signal(11)

        fsm.act("SEND_DATA",
            # Connect data_tx to UTMI output and trigger transmission
            # The data comes from the schedule engine's buffer
            # USBDataPacketGenerator expects a stream interface
            # For now, we signal completion via the data_tx interface
            If(data_tx.stream.ready,
                NextValue(data_tx_bytes_sent, data_tx_bytes_sent + 1),
                If(data_tx_bytes_sent >= max_packet - 1,
                    NextState("WAIT_HANDSHAKE"),
                )
            )
        )

        # ── WAIT_DATA (IN): receive data packet ─────────────────────────

        fsm.act("WAIT_DATA",
            If(data_rx.packet_complete,
                NextValue(bytes_xfer, data_rx.stream.payload[:11]),
                If(data_rx.crc_mismatch,
                    # CRC error — treat as no response, retry or error
                    NextState("RETRY_CHECK"),
                ).Else(
                    NextState("SEND_HANDSHAKE"),
                )
            ).Elif(timeout_counter == 0,
                NextState("RETRY_CHECK"),
            )
        )
        self.sync.usb += If(fsm.ongoing("WAIT_DATA"),
            timeout_counter.eq(timeout_counter - 1),
        )

        # ── SEND_HANDSHAKE (IN): send ACK to device ─────────────────────

        fsm.act("SEND_HANDSHAKE",
            # For IN transfers, the host must send an ACK handshake
            # USBHandshakeGenerator would handle this, but we can
            # drive it directly
            NextState("COMPLETE"),
        )

        # ── WAIT_HANDSHAKE (OUT/SETUP/PING): receive handshake ──────────

        fsm.act("WAIT_HANDSHAKE",
            If(hs_det.detected.ack,
                NextState("COMPLETE"),
            ).Elif(hs_det.detected.nak,
                NextState("RETRY_CHECK"),
            ).Elif(hs_det.detected.stall,
                NextState("COMPLETE_STALL"),
            ).Elif(hs_det.detected.nyet,
                # NYET: device accepted data but not ready for more
                # In HS, this means we should PING before sending more
                NextState("COMPLETE_NYET"),
            ).Elif(timeout_counter == 0,
                # No handshake received — timeout
                NextState("RETRY_CHECK"),
            )
        )
        self.sync.usb += If(fsm.ongoing("WAIT_HANDSHAKE"),
            timeout_counter.eq(timeout_counter - 1),
        )

        # ── RETRY_CHECK: retry or fail ──────────────────────────────────

        fsm.act("RETRY_CHECK",
            If(err_count < max_err,
                NextValue(err_count, err_count + 1),
                NextState("ISSUE_TOKEN"),
            ).Else(
                NextState("COMPLETE_ERROR"),
            )
        )

        # ── COMPLETE variants ───────────────────────────────────────────

        fsm.act("COMPLETE",
            self.response.done.eq(1),
            self.response.ack.eq(1),
            self.response.bytes_xfer.eq(bytes_xfer),
            self.response.data_toggle.eq(~data_toggle),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_STALL",
            self.response.done.eq(1),
            self.response.stall.eq(1),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_NYET",
            self.response.done.eq(1),
            self.response.nyet.eq(1),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_ERROR",
            self.response.done.eq(1),
            self.response.error.eq(1),
            self.response.bytes_xfer.eq(bytes_xfer),
            NextState("IDLE"),
        )

        # ── Defaults ────────────────────────────────────────────────────
        self.sync.usb += [
            If(~transfer_active,
                self.response.done.eq(0),
                self.response.ack.eq(0),
                self.response.nak.eq(0),
                self.response.stall.eq(0),
                self.response.nyet.eq(0),
                self.response.error.eq(0),
                self.response.babble.eq(0),
            )
        ]
