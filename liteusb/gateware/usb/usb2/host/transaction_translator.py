#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" Integrated Transaction Translator (TT) for the EHCI host controller.

Executes FS/LS bus transactions on behalf of the transfer engine by
temporarily reconfiguring the ULPI/UTMI PHY to FS/LS mode, running the
token/data/handshake phases, and returning the result.

Architecture
------------
The TT is an internal peripheral of the EHCI host controller:

  Transfer Engine → request (token + data) → TT → FS/LS transaction → PHY
  Transfer Engine ← response (status + IN data) ← TT ← PHY response

Because the translation happens inside the root hub, no SPLIT tokens
appear on the wire (unlike a hub TT, which is driven by SPLITs from the
host controller).

While the TT owns the bus it asserts ``sof_hold`` so the SOF generator
queues the next SOF until the FS/LS transaction completes.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBPacketID, USBSpeed
from ....interface.utmi import UTMIOperatingMode, UTMITerminationSelect
from .token_generator import USBCRC5


# Handshake packet bytes: PID in the low nibble, complement in the high.
_HANDSHAKE_ACK   = 0xD2
_HANDSHAKE_NAK   = 0x5A
_HANDSHAKE_STALL = 0x1E
_HANDSHAKE_NYET  = 0x96


class TransactionTranslator(Module):
    """ Integrated Transaction Translator.

    Executes an FS/LS token (+ data) transaction and reports the result.

    Interface
    ---------
    request_valid : Signal() input
        Transaction request from the transfer engine.
    request_pid : Signal(4) input
        Token PID (IN/OUT/SETUP) for the FS/LS transaction.
    request_address / request_endpoint : device/endpoint.
    request_speed : Signal(2) input
        USBSpeed.FULL or USBSpeed.LOW.
    request_data_toggle : Signal() input
        DATA0/DATA1 toggle (drives the data PID byte for OUT/SETUP).

    request_tx_data / _valid / _last : input stream
        Transmit data bytes (OUT/SETUP data phase).

    response_done / _ack / _nak / _stall / _error : Signal() output
        Result of the transaction.
    response_rx_data / _rx_valid : output stream
        Received data bytes (IN data phase).

    busy : Signal() output
        TT owns the PHY.
    sof_hold : Signal() output
        Hold off SOF generation while the TT is busy.

    # PHY interface
    utmi_xcvr_select / utmi_term_select / utmi_op_mode : outputs
    utmi_tx_valid / utmi_tx_data : outputs
    utmi_tx_ready / utmi_rx_data / utmi_rx_valid / utmi_rx_active /
    utmi_line_state : inputs
    """

    # Response timeout in UTMI clock cycles, per device speed
    _TIMEOUT_HS = 92     # ~1.5 µs at 60 MHz
    _TIMEOUT_FS = 960    # ~16 µs
    _TIMEOUT_LS = 7680   # ~128 µs

    def __init__(self):
        # ── Request interface ───────────────────────────────────────────

        self.request_valid       = Signal()
        self.request_pid         = Signal(4)
        self.request_address     = Signal(7)
        self.request_endpoint    = Signal(4)
        self.request_speed       = Signal(2)
        self.request_data_toggle = Signal()

        self.request_tx_data     = Signal(8)
        self.request_tx_valid    = Signal()
        self.request_tx_last     = Signal()

        # ── Response interface ──────────────────────────────────────────

        self.response_done       = Signal()
        self.response_ack        = Signal()
        self.response_nak        = Signal()
        self.response_stall      = Signal()
        self.response_error      = Signal()
        self.response_rx_data    = Signal(8)
        self.response_rx_valid   = Signal()

        # ── Control ─────────────────────────────────────────────────────

        self.busy                = Signal()
        self.sof_hold            = Signal()

        # ── PHY interface ───────────────────────────────────────────────

        self.utmi_xcvr_select    = Signal(2)
        self.utmi_term_select    = Signal()
        self.utmi_op_mode        = Signal(2)
        self.utmi_tx_valid       = Signal()
        self.utmi_tx_data        = Signal(8)
        self.utmi_tx_ready       = Signal()
        self.utmi_rx_data        = Signal(8)
        self.utmi_rx_valid       = Signal()
        self.utmi_rx_active      = Signal()
        self.utmi_line_state     = Signal(2)

    def do_finalize(self):
        # ── Token CRC-5 ─────────────────────────────────────────────────

        # Wire format (USB 2.0 §8.3.2): ADDR in bits [6:0], ENDP in [10:7].
        token_payload = Cat(self.request_address, self.request_endpoint)
        self.submodules.crc5 = crc5 = USBCRC5(width=11)
        self.comb += crc5.data.eq(token_payload)

        # ── Token bytes ─────────────────────────────────────────────────

        pid = Signal(4)
        tx_byte0 = Signal(8)  # PID | ~PID
        tx_byte1 = Signal(8)  # payload[7:0]
        tx_byte2 = Signal(8)  # CRC5 | payload[10:8]

        self.comb += [
            tx_byte0.eq(Cat(pid, ~pid)),
            tx_byte1.eq(token_payload[0:8]),
            tx_byte2.eq(Cat(token_payload[8:11], crc5.crc)),
        ]

        # ── Data PID byte (DATA0 = 0xC3, DATA1 = 0x4B) ──────────────────

        data_pid_byte = Signal(8)
        self.comb += data_pid_byte.eq(
            Mux(self.request_data_toggle, 0x4B, 0xC3))

        # ── Timeout ─────────────────────────────────────────────────────

        timeout       = Signal(16)
        timeout_limit = Signal(16)

        self.comb += [
            If(self.request_speed == USBSpeed.FULL,
                timeout_limit.eq(self._TIMEOUT_FS),
            ).Else(
                timeout_limit.eq(self._TIMEOUT_LS),
            )
        ]

        # ── FSM ─────────────────────────────────────────────────────────

        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.tt_fsm = fsm

        self.comb += [
            self.busy.eq(~fsm.ongoing("IDLE")),
            self.sof_hold.eq(~fsm.ongoing("IDLE")),
        ]

        # Defaults (overridden by FSM states)
        self.comb += [
            self.utmi_tx_valid.eq(0),
            self.utmi_tx_data.eq(0),
        ]

        # ── IDLE ────────────────────────────────────────────────────────
        fsm.act("IDLE",
            NextValue(self.response_done, 0),
            NextValue(self.response_rx_valid, 0),
            NextValue(self.utmi_op_mode, UTMIOperatingMode.NORMAL),
            NextValue(self.utmi_term_select, UTMITerminationSelect.LS_FS_NORMAL),
            NextValue(self.utmi_xcvr_select, USBSpeed.FULL),
            If(self.request_valid,
                NextValue(pid, self.request_pid),
                NextValue(timeout, 0),
                NextValue(self.utmi_xcvr_select, self.request_speed),
                NextState("SEND_BYTE0"),
            )
        )

        # ── Token transmission: one byte per state, advance on tx_ready ─
        fsm.act("SEND_BYTE0",
            self.utmi_tx_valid.eq(1),
            self.utmi_tx_data.eq(tx_byte0),
            If(self.utmi_tx_ready,
                NextState("SEND_BYTE1"),
            )
        )
        fsm.act("SEND_BYTE1",
            self.utmi_tx_valid.eq(1),
            self.utmi_tx_data.eq(tx_byte1),
            If(self.utmi_tx_ready,
                NextState("SEND_BYTE2"),
            )
        )
        fsm.act("SEND_BYTE2",
            self.utmi_tx_valid.eq(1),
            self.utmi_tx_data.eq(tx_byte2),
            If(self.utmi_tx_ready,
                NextValue(timeout, 0),
                If(pid == USBPacketID.IN,
                    NextState("WAIT_RX_DATA"),
                ).Else(
                    NextState("SEND_DATA_PID"),
                )
            )
        )

        # ── OUT/SETUP data phase: DATAx PID byte, payload, CRC16 ───────
        # NOTE: payload bytes come from the request stream; CRC16 is
        # appended by the caller-side data path in a full implementation.
        fsm.act("SEND_DATA_PID",
            self.utmi_tx_valid.eq(1),
            self.utmi_tx_data.eq(data_pid_byte),
            If(self.utmi_tx_ready,
                NextState("SEND_TX_DATA"),
            )
        )

        fsm.act("SEND_TX_DATA",
            self.utmi_tx_valid.eq(self.request_tx_valid),
            self.utmi_tx_data.eq(self.request_tx_data),
            If(self.request_tx_valid & self.utmi_tx_ready & self.request_tx_last,
                NextValue(timeout, 0),
                NextState("WAIT_HANDSHAKE"),
            )
        )

        # ── IN data phase: receive bytes while the PHY is active ────────
        fsm.act("WAIT_RX_DATA",
            NextValue(timeout, timeout + 1),
            If(self.utmi_rx_valid,
                NextValue(self.response_rx_data, self.utmi_rx_data),
                NextValue(self.response_rx_valid, 1),
                NextValue(timeout, 0),
            ).Else(
                NextValue(self.response_rx_valid, 0),
            ),
            # End of packet: rx_active drops after bytes were received
            If(~self.utmi_rx_active & self.response_rx_valid,
                NextState("COMPLETE_ACK"),
            ).Elif(timeout >= timeout_limit,
                NextState("COMPLETE_ERROR"),
            )
        )

        # ── Handshake decode ────────────────────────────────────────────
        fsm.act("WAIT_HANDSHAKE",
            NextValue(timeout, timeout + 1),
            If(self.utmi_rx_valid,
                Case(self.utmi_rx_data, {
                    _HANDSHAKE_ACK:   NextState("COMPLETE_ACK"),
                    _HANDSHAKE_NAK:   NextState("COMPLETE_NAK"),
                    _HANDSHAKE_STALL: NextState("COMPLETE_STALL"),
                    "default":        NextState("COMPLETE_ERROR"),
                }),
            ).Elif(timeout >= timeout_limit,
                NextState("COMPLETE_ERROR"),
            )
        )

        # ── COMPLETE states: restore PHY to HS and report ───────────────
        for state, flag in (("COMPLETE_ACK",   self.response_ack),
                            ("COMPLETE_NAK",   self.response_nak),
                            ("COMPLETE_STALL", self.response_stall),
                            ("COMPLETE_ERROR", self.response_error)):
            fsm.act(state,
                NextValue(self.utmi_xcvr_select, USBSpeed.HIGH),
                NextValue(self.utmi_term_select, UTMITerminationSelect.HS_NORMAL),
                NextValue(self.response_done, 1),
                NextValue(flag, 1),
                NextState("IDLE"),
            )
