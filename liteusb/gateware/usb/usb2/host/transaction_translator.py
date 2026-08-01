#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Integrated Transaction Translator (TT) for the EHCI host controller.

Converts SPLIT start-split tokens into actual FS/LS bus transactions
by temporarily reconfiguring the ULPI/UTMI PHY to FS/LS mode,
executing the bus transaction, and returning the result.

Architecture
------------
The TT is an internal peripheral of the EHCI host controller.
It operates between the HS-capable schedule processor and the PHY:

  Schedule Processor (HS) → SPLIT Token → TT → FS/LS Transaction → PHY
                                          ↓
  Schedule Processor (HS) ← Result ← Complete-Split ← TT ← PHY response

The TT:
1. Receives a start-split request (token + data for OUT, token only for IN)
2. Switches PHY to FS/LS mode (xcvr_select, term_select)
3. Executes the bus transaction at FS/LS speed
4. Switches PHY back to HS mode
5. Reports the result (ACK/NAK/STALL/error, received data for IN transfers)

TT Think Time model:
- The EHCI spec allows the TT to delay transaction execution within a
  microframe (the "TT think time"). This implementation queues the SOF
  during FS/LS transactions and defers it until the TT completes.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBPacketID, USBSpeed
from ....interface.utmi import (
    UTMITransmitInterface, UTMIOperatingMode, UTMITerminationSelect,
)


class TransactionTranslator(Module):
    """ Integrated Transaction Translator.

    Converts HS-speed SPLIT tokens into FS/LS bus transactions.

    Interface
    ---------
    request_valid : Signal() input
        Start-split request from the transfer engine.
    request_pid : Signal(4) input
        Token PID (IN/OUT/SETUP) for the FS/LS transaction.
    request_address : Signal(7) input
        Device address.
    request_endpoint : Signal(4) input
        Endpoint number.
    request_speed : Signal(2) input
        Device speed (USBSpeed.FULL or USBSpeed.LOW)
    request_data_toggle : Signal() input
        DATA0/DATA1 toggle.

    request_tx_data : Signal(8) input
        Transmit data byte (for OUT/SETUP).
    request_tx_valid : Signal() input
        Transmit data valid.
    request_tx_last : Signal() input
        Last byte of transmit data.

    response_done : Signal() output
        Transaction complete.
    response_ack : Signal() output
        Device ACKed.
    response_nak : Signal() output
        Device NAKed.
    response_stall : Signal() output
        Device STALLed.
    response_error : Signal() output
        Timeout or CRC error.
    response_rx_data : Signal(8) output
        Received data byte (for IN transfers).
    response_rx_valid : Signal() output
        Received data valid.

    busy : Signal() output
        TT is executing a transaction.
    sof_hold : Signal() output
        Hold off SOF generation while TT is busy.

    # PHY control (to UTMI/ULPI bus)
    utmi_xcvr_select : Signal(2) output
    utmi_term_select : Signal() output
    utmi_op_mode : Signal(2) output
    utmi_tx_valid : Signal() output
    utmi_tx_data : Signal(8) output
    utmi_rx_data : Signal(8) input
    utmi_rx_valid : Signal() input
    utmi_rx_active : Signal() input
    utmi_line_state : Signal(2) input
    """

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
        self.utmi_rx_data        = Signal(8)
        self.utmi_rx_valid       = Signal()
        self.utmi_rx_active      = Signal()
        self.utmi_line_state     = Signal(2)

    def do_finalize(self):
        # ── Token CRC-5 ─────────────────────────────────────────────────

        # Build token payload: ADDR(7) + ENDP(4) = 11 bits
        token_payload = Cat(self.request_endpoint, self.request_address)
        # CRC5 is computed combinatorially — reuse the existing CRC5 module
        from .token_generator import USBCRC5
        self.submodules.crc5 = crc5 = USBCRC5(width=11)
        self.comb += crc5.data.eq(token_payload)

        # ── Token bytes ─────────────────────────────────────────────────

        pid = self.request_pid
        tx_byte0 = Signal(8)  # PID | ~PID
        tx_byte1 = Signal(8)  # payload[7:0]
        tx_byte2 = Signal(8)  # payload[10:8] | CRC5

        self.comb += [
            tx_byte0.eq(Cat(pid, ~pid)),
            tx_byte1.eq(token_payload[0:8]),
            tx_byte2.eq(Cat(crc5.crc, token_payload[8:11])),
        ]

        # ── Timeout counter ─────────────────────────────────────────────

        timeout = Signal(16)
        TIMEOUT_HS = 92   # ~1.5µs at 60MHz
        TIMEOUT_FS = 800  # ~13µs at 60MHz (FS bit times are ~670ns)
        TIMEOUT_LS = 6400 # ~106µs (LS bit times are ~6.7µs)

        timeout_limit = Signal(16)

        self.comb += [
            If(self.request_speed == USBSpeed.HIGH,
                timeout_limit.eq(TIMEOUT_HS),
            ).Elif(self.request_speed == USBSpeed.FULL,
                timeout_limit.eq(TIMEOUT_FS),
            ).Else(
                timeout_limit.eq(TIMEOUT_LS),
            )
        ]

        # ── Receive data capture ────────────────────────────────────────

        rx_data_reg  = Signal(8)
        rx_valid_reg = Signal()
        rx_byte_count = Signal(11)
        rx_pid       = Signal(8)

        self.sync.usb += [
            If(self.utmi_rx_valid,
                rx_data_reg.eq(self.utmi_rx_data),
                rx_valid_reg.eq(1),
            ).Else(
                rx_valid_reg.eq(0),
            )
        ]

        # ── FSM ─────────────────────────────────────────────────────────

        fsm = FSM(reset_state="IDLE")
        self.submodules.tt_fsm = fsm

        self.comb += self.busy.eq(~fsm.ongoing("IDLE"))
        self.comb += self.sof_hold.eq(~fsm.ongoing("IDLE"))

        # IDLE
        fsm.act("IDLE",
            NextValue(self.response_done, 0),
            NextValue(self.utmi_tx_valid, 0),
            NextValue(self.utmi_op_mode, UTMIOperatingMode.NORMAL),
            NextValue(self.utmi_term_select, UTMITerminationSelect.LS_FS_NORMAL),
            NextValue(self.utmi_xcvr_select, USBSpeed.FULL),
            If(self.request_valid,
                # Switch PHY to FS/LS mode
                NextValue(self.utmi_xcvr_select, self.request_speed),
                NextValue(self.utmi_term_select, UTMITerminationSelect.LS_FS_NORMAL),
                NextValue(timeout, 0),
                NextValue(rx_byte_count, 0),
                NextState("SEND_TOKEN"),
            )
        )

        # SEND_TOKEN: send 3-byte token packet
        fsm.act("SEND_TOKEN",
            NextValue(self.utmi_tx_valid, 1),
            NextValue(self.utmi_tx_data, tx_byte0),
            If(self.utmi_rx_active,  # PHY confirms TX advancement
                NextValue(self.utmi_tx_data, tx_byte1),
                NextState("SEND_TOKEN_1"),
            )
        )

        fsm.act("SEND_TOKEN_1",
            NextValue(self.utmi_tx_valid, 1),
            NextValue(self.utmi_tx_data, tx_byte1),
            If(self.utmi_rx_active,
                NextValue(self.utmi_tx_data, tx_byte2),
                NextState("SEND_TOKEN_2"),
            )
        )

        fsm.act("SEND_TOKEN_2",
            NextValue(self.utmi_tx_valid, 1),
            NextValue(self.utmi_tx_data, tx_byte2),
            If(self.utmi_rx_active,
                NextValue(self.utmi_tx_valid, 0),
                NextValue(timeout, 0),
                If(self.request_pid == USBPacketID.IN,
                    NextState("WAIT_RX_DATA"),
                ).Elif((self.request_pid == USBPacketID.OUT) |
                       (self.request_pid == USBPacketID.SETUP),
                    NextState("SEND_TX_DATA"),
                )
            )
        )

        # SEND_TX_DATA: transmit OUT/SETUP data packet to FS/LS device
        fsm.act("SEND_TX_DATA",
            NextValue(self.utmi_tx_valid, self.request_tx_valid),
            NextValue(self.utmi_tx_data, self.request_tx_data),
            If(self.request_tx_last & self.request_tx_valid,
                NextValue(self.utmi_tx_valid, 0),
                NextValue(timeout, 0),
                NextState("WAIT_HANDSHAKE"),
            )
        )

        # WAIT_RX_DATA: receive IN data from FS/LS device
        fsm.act("WAIT_RX_DATA",
            NextValue(self.utmi_tx_valid, 0),
            If(rx_valid_reg,
                NextValue(self.response_rx_data, rx_data_reg),
                NextValue(self.response_rx_valid, 1),
                NextValue(rx_byte_count, rx_byte_count + 1),
                NextValue(timeout, 0),
                # In a full implementation, detect EOP/PID/CRC
                # For now, transition after first byte
                NextState("COMPLETE_ACK"),
            ).Elif(timeout >= timeout_limit,
                NextState("COMPLETE_ERROR"),
            )
        )
        self.sync.usb += If(fsm.ongoing("WAIT_RX_DATA"),
            timeout.eq(timeout + 1),
        )

        # WAIT_HANDSHAKE: wait for ACK/NAK/STALL from FS/LS device
        fsm.act("WAIT_HANDSHAKE",
            NextValue(self.utmi_tx_valid, 0),
            If(rx_valid_reg,
                # Decode handshake PID from received byte
                # ACK   = 0xD2 (0010_1101 → PID=0010, check=1101)
                # NAK   = 0x5A (1010_0101 → PID=1010, check=0101)
                # STALL = 0x1E (1110_0001 → PID=1110, check=0001)
                # NYET  = 0x96 (0110_1001)
                # The PID is the lower nibble
                Case(rx_data_reg[0:4], {
                    USBPacketID.ACK:   NextState("COMPLETE_ACK"),
                    USBPacketID.NAK:   NextState("COMPLETE_NAK"),
                    USBPacketID.STALL: NextState("COMPLETE_STALL"),
                    "default":         NextState("COMPLETE_ERROR"),
                }),
            ).Elif(timeout >= timeout_limit,
                NextState("COMPLETE_ERROR"),
            )
        )
        self.sync.usb += If(fsm.ongoing("WAIT_HANDSHAKE"),
            timeout.eq(timeout + 1),
        )

        # COMPLETE states — restore PHY to HS and signal result
        fsm.act("COMPLETE_ACK",
            NextValue(self.utmi_xcvr_select, USBSpeed.HIGH),
            NextValue(self.utmi_term_select, UTMITerminationSelect.HS_NORMAL),
            NextValue(self.response_done, 1),
            NextValue(self.response_ack, 1),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_NAK",
            NextValue(self.utmi_xcvr_select, USBSpeed.HIGH),
            NextValue(self.utmi_term_select, UTMITerminationSelect.HS_NORMAL),
            NextValue(self.response_done, 1),
            NextValue(self.response_nak, 1),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_STALL",
            NextValue(self.utmi_xcvr_select, USBSpeed.HIGH),
            NextValue(self.utmi_term_select, UTMITerminationSelect.HS_NORMAL),
            NextValue(self.response_done, 1),
            NextValue(self.response_stall, 1),
            NextState("IDLE"),
        )
        fsm.act("COMPLETE_ERROR",
            NextValue(self.utmi_xcvr_select, USBSpeed.HIGH),
            NextValue(self.utmi_term_select, UTMITerminationSelect.HS_NORMAL),
            NextValue(self.response_done, 1),
            NextValue(self.response_error, 1),
            NextState("IDLE"),
        )
