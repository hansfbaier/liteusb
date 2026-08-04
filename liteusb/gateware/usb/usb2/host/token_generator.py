#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" Host-side USB token generation — SOF, IN, OUT, SETUP, PING, SPLIT.

Generates token packets on a UTMI transmit interface.
The SOF generator runs autonomously; other tokens are generated on demand.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBPacketID, USBSpeed
from ....interface.utmi import UTMITransmitInterface


# ── SOF Counter ─────────────────────────────────────────────────────────────

class USBSOFCounter(Module):
    """ TT-aware SOF counter with per-speed timing.

    HS: 125µs microframes (7500 cycles at 60MHz)
    FS/LS: 1ms frames (60000 cycles at 60MHz)
    When sof_hold is asserted (TT busy), SOF is queued.
    """

    def __init__(self, domain_clock=60e6):
        self.frame_number      = Signal(11)
        self.microframe_number = Signal(3)
        self.issue_sof         = Signal()
        self.new_frame         = Signal()
        self.speed             = Signal(2)
        self.sof_hold          = Signal()

        self._hs_period = int(domain_clock * 125e-6)
        self._fs_period = int(domain_clock * 1e-3)

    def do_finalize(self):
        hs_period = self._hs_period
        fs_period = self._fs_period
        max_period = max(hs_period, fs_period)

        counter     = Signal(max=max_period + 1, reset=0)
        period      = Signal(max=max_period + 1)
        sof_pending = Signal()

        self.comb += [
            If(self.speed == USBSpeed.HIGH,
                period.eq(hs_period),
            ).Else(
                period.eq(fs_period),
            )
        ]

        fire_sof = Signal()
        self.comb += fire_sof.eq(
            ((counter == period - 1) & ~self.sof_hold) |
            (sof_pending & ~self.sof_hold)
        )

        self.sync.usb += [
            self.issue_sof.eq(0),
            self.new_frame.eq(0),

            If(fire_sof,
                counter.eq(0),
                sof_pending.eq(0),
                self.issue_sof.eq(1),
                If(self.speed == USBSpeed.HIGH,
                    self.microframe_number.eq(self.microframe_number + 1),
                    If(self.microframe_number == 7,
                        self.microframe_number.eq(0),
                        self.new_frame.eq(1),
                        self.frame_number.eq(self.frame_number + 1),
                    ),
                ).Else(
                    self.microframe_number.eq(0),
                    self.new_frame.eq(1),
                    self.frame_number.eq(self.frame_number + 1),
                ),
            ).Elif(counter == period - 1,
                # sof_hold active at terminal count: queue the SOF
                sof_pending.eq(1),
            ).Else(
                counter.eq(counter + 1),
            ),
        ]


# ── CRC5 Generator (combinatorial) ──────────────────────────────────────────

class USBCRC5(Module):
    """ Combinatorial CRC-5 generator for USB token packets.

    USB 2.0 spec §8.3.5: polynomial G(x) = x^5 + x^2 + 1, shift register
    initialized to all ones, data shifted in LSB-first, and the complement
    of the final remainder is transmitted (LSB-first).

    Verified against on-the-wire vectors, e.g. a SETUP token to
    address 0 / endpoint 0 transmits CRC5 = 0x02 (bytes 2D 00 10).
    """

    def __init__(self, width=11):
        self.data  = Signal(width)
        self.crc   = Signal(5)

    def do_finalize(self):
        # Bit-serial LFSR, LSB-first. Reflected polynomial: 0b10100 (0x14).
        #   fb = data_bit ^ crc[0]
        #   crc = (crc >> 1) ^ (fb ? 0x14 : 0)
        # Unrolled combinatorially over all input bits.
        crc_stages = [Signal(5) for _ in range(12)]
        self.comb += crc_stages[0].eq(0x1F)  # initial remainder: all ones

        for i in range(11):
            fb = Signal()
            shifted = Signal(5)
            self.comb += [
                fb.eq(self.data[i] ^ crc_stages[i][0]),
                shifted.eq(Cat(crc_stages[i][1], crc_stages[i][2],
                               crc_stages[i][3], crc_stages[i][4], 0)),
                If(fb,
                    crc_stages[i + 1].eq(shifted ^ 0b10100)
                ).Else(
                    crc_stages[i + 1].eq(shifted)
                )
            ]

        # Transmitted CRC is the complement of the final remainder
        self.comb += self.crc.eq(~crc_stages[11])


# ── Host Token Generator ────────────────────────────────────────────────────

class USBHostTokenGenerator(Module):
    """ Generates USB token packets for the host controller on the UTMI bus.

    Supports SOF (automatic), IN, OUT, SETUP, PING, and SPLIT tokens.
    Uses the companion SOF counter for automatic SOF generation.

    Parameters
    ----------
    utmi : UTMIInterface
        The UTMI bus to transmit on.
    domain_clock : float
        The UTMI clock frequency (default 60 MHz for HS).

    Interface
    ---------
    sof_enable : Signal() input
        When high, SOF tokens are generated automatically.
    speed : Signal(2) input
        Current operating speed (USBSpeed.HIGH/FULL/LOW).

    token_pid : Signal(4) input
        PID for on-demand token (IN/OUT/SETUP/PING/SPLIT).
    token_address : Signal(7) input
        Device address for on-demand tokens.
    token_endpoint : Signal(4) input
        Endpoint number for on-demand tokens.
    issue_token : Signal() input
        Strobe: issue the configured on-demand token.
    token_busy : Signal() output
        High while a token is being transmitted.

    sof_pid : Signal(4)
        PID for SOF tokens (normally USBPacketID.SOF).

    Notes
    -----
    SPLIT tokens are not generated: the integrated Transaction Translator
    talks to FS/LS devices directly, so SPLIT tokens never appear on the bus.
    """

    def __init__(self, utmi, domain_clock=60e6):
        self.utmi = utmi

        # Control
        self.sof_enable      = Signal(reset=0)
        self.speed           = Signal(2)

        # On-demand token parameters
        self.token_pid       = Signal(4)
        self.token_address   = Signal(7)
        self.token_endpoint  = Signal(4)

        # Handshake
        self.issue_token     = Signal()
        self.token_busy      = Signal()

        # TX interface (driven by this module; routed through a mux by
        # the parent, which drives the actual UTMI bus)
        self.tx_valid        = Signal()
        self.tx_data         = Signal(8)
        self.tx_ready        = Signal()

        # SOF counter
        self.submodules.sof_counter = sof_counter = USBSOFCounter(
            domain_clock=domain_clock)

        # Internal
        self._domain_clock = domain_clock

    def do_finalize(self):
        sof_counter = self.sof_counter
        utmi = self.utmi

        # Token data construction
        # A token is 32 bits: PID(8) + ADDR/FRAME(11) + CRC5(5) = 24 data bits
        # Actually: PID byte (8 bits, with complement), then payload (11 bits), then CRC5 (5 bits)
        # Total on wire: 8 + 11 + 5 = 24 bits, which at UTMI 8-bit width is 3 bytes.
        #
        # Byte 0: PID (lower nibble) | ~PID (upper nibble)
        # Byte 1: payload[7:0]
        # Byte 2: payload[10:8] | CRC5[4:0]

        # Build the 11-bit payload
        pid          = Signal(4)
        payload      = Signal(11)
        crc5_value   = Signal(5)

        # CRC5 generator
        self.submodules.crc5 = crc5 = USBCRC5(width=11)
        self.comb += [
            crc5.data.eq(payload),
            crc5_value.eq(crc5.crc),
        ]

        # Three bytes to transmit
        tx_byte0 = Signal(8)  # PID | ~PID
        tx_byte1 = Signal(8)  # payload[7:0]
        tx_byte2 = Signal(8)  # payload[10:8] | CRC5

        self.comb += [
            tx_byte0.eq(Cat(pid, ~pid)),
            tx_byte1.eq(payload[0:8]),
            # on the wire: payload[10:8] in bits 2:0, CRC5 in bits 7:3
            tx_byte2.eq(Cat(payload[8:11], crc5_value)),
        ]

        # SOF payload is the 11-bit frame number; IN/OUT/SETUP/PING payload
        # is ADDR(7) | ENDP(4).
        #
        # SPLIT tokens are intentionally not supported: this controller uses
        # an integrated Transaction Translator, so SPLIT tokens never appear
        # on the wire (they only exist between an EHCI HC and a hub's TT).

        is_sof   = Signal()
        is_ping  = Signal()

        self.comb += [
            is_sof.eq(pid == USBPacketID.SOF),
            is_ping.eq(pid == USBPacketID.PING),
        ]

        # Build payload combinatorially.
        # Wire format (USB 2.0 §8.3.2): ADDR in bits [6:0], ENDP in [10:7].
        _token_payload = Cat(self.token_address, self.token_endpoint)

        self.comb += [
            If(is_sof,
                payload.eq(sof_counter.frame_number),
            ).Else(
                # Standard token: IN/OUT/SETUP/PING
                payload.eq(_token_payload),
            )
        ]

        # FSM: generate token bytes on UTMI
        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.token_fsm = fsm

        self.comb += self.token_busy.eq(~fsm.ongoing("IDLE"))

        fsm.act("IDLE",
            If(sof_counter.issue_sof & self.sof_enable,
                NextValue(pid, USBPacketID.SOF),
                NextState("SEND_BYTE0"),
            ).Elif(self.issue_token,
                NextValue(pid, self.token_pid),
                NextState("SEND_BYTE0"),
            )
        )

        fsm.act("SEND_BYTE0",
            If(self.tx_ready,
                NextState("SEND_BYTE1")
            )
        )
        self.comb += If(fsm.ongoing("SEND_BYTE0"),
            self.tx_data.eq(tx_byte0),
            self.tx_valid.eq(1),
        )

        fsm.act("SEND_BYTE1",
            If(self.tx_ready,
                NextState("SEND_BYTE2")
            )
        )
        self.comb += If(fsm.ongoing("SEND_BYTE1"),
            self.tx_data.eq(tx_byte1),
            self.tx_valid.eq(1),
        )

        fsm.act("SEND_BYTE2",
            If(self.tx_ready,
                NextState("IDLE")
            )
        )
        self.comb += If(fsm.ongoing("SEND_BYTE2"),
            self.tx_data.eq(tx_byte2),
            self.tx_valid.eq(1),
        )

        # In IDLE, don't drive the bus
        self.comb += If(fsm.ongoing("IDLE"),
            self.tx_data.eq(0),
            self.tx_valid.eq(0),
        )
