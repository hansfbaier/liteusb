#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" Host-side USB reset sequencer and speed detection.

Drives the bus reset sequence, responds to device chirp during
high-speed negotiation, and determines the attached device speed
(High-Speed, Full-Speed, or Low-Speed).

References
----------
USB 2.0 Spec, Section 7.1.7.3 (Reset Signaling)
USB 2.0 Spec, Section 7.1.7.5 (High-Speed Detection Handshake)

Host reset sequence:
  1. Drive SE0 for >= 10 ms (T_DRST) — bus reset.  SE0 is produced by
     putting the UTMI transmitter in NON_DRIVING mode; the host's 15k
     pull-downs hold both lines low.
  2. Release the bus; watch the line state:
     - J  → Full-Speed device (D+ pull-up) — done.
     - K  → either a Low-Speed device (D- pull-up) or an HS-capable
            device chirping K.  Disambiguate by duration: the device
            chirp K lasts 1-7 ms (T_DCHIRP); an LS pull-up persists.
  3. HS handshake: when the device chirp K ends, the host drives
     alternating chirp K / chirp J, ~50 µs each, for 3 K-J pairs
     (T_CHIRPK / T_CHIRPJ are 40-60 µs), then switches the port to
     High-Speed terminations.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBSpeed
from ....interface.utmi import UTMIOperatingMode, UTMITerminationSelect


class HostResetSequencer(Module):
    """ Host-side bus reset and speed detection.

    Parameters
    ----------
    domain_clock : float
        UTMI clock frequency (default 60 MHz).

    Interface
    ---------
    start : Signal() input
        Strobe to begin the reset/detection sequence.
    line_state : Signal(2) input
        UTMI line_state from the PHY.
    bus_busy : Signal() input
        Hold-off: other transmitter is using the bus.

    current_speed : Signal(2) output
        Detected speed (USBSpeed.HIGH/FULL/LOW).
    bus_reset : Signal() output
        High while the bus reset (SE0) is being driven.
    hs_detected : Signal() output
        Pulses high when HS negotiation succeeds.
    done : Signal() output
        Pulses high when detection is complete.

    # UTMI control outputs (to drive onto the PHY)
    op_mode : Signal(2) output
    term_select : Signal() output
    xcvr_select : Signal(2) output
    tx_valid : Signal() output
    tx_data : Signal(8) output
    """

    # Timing constants at 60 MHz (scaled by domain_clock)
    _CYCLES_10_MILLISECONDS  = 600000    # T_DRST minimum
    _CYCLES_2P5_MILLISECONDS = 150000    # device chirp detection window
    _CYCLES_7_MILLISECONDS   = 420000    # max device chirp K (T_DCHIRP)
    _CHIRP_PAIR_CYCLES       = 3000      # 50 µs per chirp K/J (40-60 µs)
    _CHIRP_PAIRS             = 3         # K-J pairs required

    def __init__(self, domain_clock=60e6):
        scale = domain_clock / 60e6
        self._CYCLES_10_MS  = int(self._CYCLES_10_MILLISECONDS * scale)
        self._CYCLES_2P5_MS = int(self._CYCLES_2P5_MILLISECONDS * scale)
        self._CYCLES_7_MS   = int(self._CYCLES_7_MILLISECONDS * scale)
        self._CHIRP_CYCLES  = int(self._CHIRP_PAIR_CYCLES * scale)

        # ── I/O ─────────────────────────────────────────────────────────

        self.start          = Signal()
        self.line_state     = Signal(2)
        self.bus_busy       = Signal()

        self.current_speed  = Signal(2, reset=USBSpeed.FULL)
        self.bus_reset      = Signal()
        self.hs_detected    = Signal()
        self.done           = Signal()

        # UTMI control outputs
        self.op_mode        = Signal(2)
        self.term_select    = Signal()
        self.xcvr_select    = Signal(2)
        self.tx_valid       = Signal()
        self.tx_data        = Signal(8)

        # Line state constants
        self._SE0 = 0b00
        self._J   = 0b01
        self._K   = 0b10

    def do_finalize(self):
        timer       = Signal(max=self._CYCLES_10_MS + 1)
        chirp_pairs = Signal(3)

        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # ── IDLE ────────────────────────────────────────────────────────
        fsm.act("IDLE",
            NextValue(self.op_mode, UTMIOperatingMode.NORMAL),
            NextValue(self.term_select, UTMITerminationSelect.LS_FS_NORMAL),
            NextValue(self.xcvr_select, USBSpeed.FULL),
            NextValue(self.tx_valid, 0),
            NextValue(self.tx_data, 0),
            NextValue(self.done, 0),
            NextValue(self.bus_reset, 0),
            NextValue(self.hs_detected, 0),
            If(self.start & ~self.bus_busy,
                NextValue(timer, 0),
                NextState("DRIVE_RESET"),
            )
        )

        # ── DRIVE_RESET: SE0 for >= 10 ms ───────────────────────────────
        # Transmitter non-driving; the 15k pull-downs create SE0.
        fsm.act("DRIVE_RESET",
            NextValue(self.op_mode, UTMIOperatingMode.NON_DRIVING),
            NextValue(self.term_select, UTMITerminationSelect.LS_FS_NORMAL),
            NextValue(self.xcvr_select, USBSpeed.FULL),
            NextValue(self.tx_valid, 0),
            NextValue(self.bus_reset, 1),
            NextValue(timer, timer + 1),
            If(timer >= self._CYCLES_10_MS,
                NextValue(timer, 0),
                NextValue(self.bus_reset, 0),
                NextValue(self.op_mode, UTMIOperatingMode.NORMAL),
                NextState("WAIT_CHIRP"),
            )
        )

        # ── WAIT_CHIRP: bus released; classify by line state ────────────
        fsm.act("WAIT_CHIRP",
            NextValue(self.tx_valid, 0),
            NextValue(timer, timer + 1),
            If(self.line_state == self._J,
                # D+ pull-up: full-speed device
                NextValue(self.current_speed, USBSpeed.FULL),
                NextState("DONE"),
            ).Elif(self.line_state == self._K,
                # D- high: LS pull-up or HS device chirp K
                NextValue(timer, 0),
                NextState("AWAIT_CHIRP_END"),
            ).Elif(timer >= self._CYCLES_2P5_MS,
                # Nothing on the bus; fall back to FS detection
                NextState("DETECT_FS_LS"),
            )
        )

        # ── AWAIT_CHIRP_END: HS chirp K lasts 1-7 ms; LS pull-up stays ──
        fsm.act("AWAIT_CHIRP_END",
            NextValue(timer, timer + 1),
            If(self.line_state != self._K,
                # Device stopped chirping: answer with host chirp K/J
                NextValue(timer, 0),
                NextValue(chirp_pairs, 0),
                NextValue(self.op_mode, UTMIOperatingMode.CHIRP),
                NextValue(self.term_select, UTMITerminationSelect.HS_CHIRP),
                NextValue(self.xcvr_select, USBSpeed.HIGH),
                NextState("HOST_CHIRP_K"),
            ).Elif(timer >= self._CYCLES_7_MS,
                # K persisted beyond the maximum chirp: low-speed device
                NextValue(self.current_speed, USBSpeed.LOW),
                NextState("DONE"),
            )
        )

        # ── HOST_CHIRP_K: drive chirp K for ~50 µs ──────────────────────
        # In chirp mode a constant 0x00 on tx_data is a chirp K.
        fsm.act("HOST_CHIRP_K",
            NextValue(self.tx_valid, 1),
            NextValue(self.tx_data, 0x00),
            NextValue(timer, timer + 1),
            If(timer >= self._CHIRP_CYCLES,
                NextValue(timer, 0),
                NextState("HOST_CHIRP_J"),
            )
        )

        # ── HOST_CHIRP_J: drive chirp J for ~50 µs ──────────────────────
        fsm.act("HOST_CHIRP_J",
            NextValue(self.tx_valid, 1),
            NextValue(self.tx_data, 0xFF),
            NextValue(timer, timer + 1),
            If(timer >= self._CHIRP_CYCLES,
                NextValue(timer, 0),
                If(chirp_pairs == self._CHIRP_PAIRS - 1,
                    # Required K-J pairs driven: switch port to High-Speed
                    NextValue(self.tx_valid, 0),
                    NextValue(self.op_mode, UTMIOperatingMode.NORMAL),
                    NextValue(self.term_select, UTMITerminationSelect.HS_NORMAL),
                    NextValue(self.xcvr_select, USBSpeed.HIGH),
                    NextValue(self.current_speed, USBSpeed.HIGH),
                    NextValue(self.hs_detected, 1),
                    NextState("DONE"),
                ).Else(
                    NextValue(chirp_pairs, chirp_pairs + 1),
                    NextState("HOST_CHIRP_K"),
                )
            )
        )

        # ── DETECT_FS_LS: no chirp seen; classify by idle line state ────
        fsm.act("DETECT_FS_LS",
            If(self.line_state == self._K,
                NextValue(self.current_speed, USBSpeed.LOW),
            ).Else(
                NextValue(self.current_speed, USBSpeed.FULL),
            ),
            NextState("DONE"),
        )

        # ── DONE ────────────────────────────────────────────────────────
        fsm.act("DONE",
            NextValue(self.tx_valid, 0),
            NextValue(self.done, 1),
            NextState("IDLE"),
        )
