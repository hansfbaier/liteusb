#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" EHCI schedule traversal engine.

Processes the EHCI Periodic and Asynchronous schedules on each microframe
boundary.  Walks the frame list, traverses linked lists of QHs/iTDs/siTDs,
and dispatches transfers to the USBHostTransferEngine.

References
----------
EHCI Spec Rev 1.0, Section 4: Operational Model
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .. import USBPacketID, USBSpeed
from .data_structures import (
    QueueHeadLayout, QueueTD, IsochronousTD,
    SplitIsochronousTD, FrameList,
)
from .transfer import HostTransferRequest, HostTransferResponse


# ── Schedule Processor ──────────────────────────────────────────────────────

class EHCIScheduleProcessor(Module):
    """ Traverses EHCI schedule data structures and dispatches transfers.

    On each microframe boundary (signalled by sof_strobe):
        1. If periodic schedule is enabled, process the periodic list
           for this microframe.
        2. If asynchronous schedule is enabled, process one async transfer.

    The schedule engine reads QH/iTD/qTD structures from a memory interface
    and writes back status/overlay updates after each transfer completes.

    Parameters
    ----------
    num_ports : int
        Number of downstream ports.
    """

    def __init__(self, num_ports=1):
        # ── Control inputs ──────────────────────────────────────────────

        self.run                 = Signal()      # USBCMD.RUN
        self.periodic_enable     = Signal()      # USBCMD.PSE
        self.async_enable        = Signal()      # USBCMD.ASE
        self.frame_list_base     = Signal(32)    # PERIODICLISTBASE
        self.async_list_addr     = Signal(32)    # ASYNCLISTADDR
        self.fls_1024            = Signal()      # Frame List Size select

        # ── Status outputs ──────────────────────────────────────────────

        self.frame_index         = Signal(14)    # FRINDEX [13:3] = entry index
        self.sof_strobe          = Signal()      # New microframe
        self.hc_halted           = Signal(reset=1)  # HC is halted
        self.periodic_status     = Signal()      # Periodic schedule active
        self.async_status        = Signal()      # Async schedule active
        self.reclamation_active  = Signal()      # Reclamation in progress
        self.iaa_pending         = Signal()      # Interrupt on Async Advance

        # ── Transfer interface ──────────────────────────────────────────

        self.transfer_request  = HostTransferRequest()
        self.transfer_response = HostTransferResponse()

        # ── Frame List (1024 × 32-bit entries) ──────────────────────────

        # In a real implementation, we read from external memory.
        # For now, we provide a simplified model.

        self._num_ports = num_ports

    def do_finalize(self):
        # ── Internal state ──────────────────────────────────────────────

        schedule_active   = Signal()
        frame_list_index  = Signal(10)  # 0..1023
        current_ptr       = Signal(32)
        microframe_in_frame = Signal(3)  # 0..7

        # ── Microframe counter ──────────────────────────────────────────

        # When sof_strobe fires, we process the schedule.
        # sof_strobe comes from the SOF counter in the token generator.

        self.sync.usb += [
            If(self.sof_strobe & self.run,
                microframe_in_frame.eq(microframe_in_frame + 1),
                If(microframe_in_frame == 7,
                    microframe_in_frame.eq(0),
                ),
                # Update frame index: lower 3 bits = microframe, upper bits = frame
                self.frame_index.eq(self.frame_index + 1),
            ),
            # HCHalted when not running
            self.hc_halted.eq(~self.run),
        ]

        # ── Schedule processing FSM ─────────────────────────────────────

        fsm = FSM(reset_state="HALTED")
        self.submodules.sched_fsm = fsm

        # HALTED: wait for RUN bit
        fsm.act("HALTED",
            If(self.run,
                NextState("IDLE"),
            )
        )

        # IDLE: wait for next microframe boundary
        fsm.act("IDLE",
            If(self.sof_strobe & self.run,
                NextState("PERIODIC"),
            )
        )

        # ── PERIODIC Schedule ───────────────────────────────────────────

        fsm.act("PERIODIC",
            If(~self.periodic_enable,
                NextState("ASYNC"),
            ).Else(
                # Process periodic schedule for this microframe
                # In real hardware:
                #   1. Read Frame List entry at current microframe index
                #   2. Walk linked list, executing transfers
                #   3. Update FRINDEX
                #
                # For now, signal periodic schedule is active
                self.periodic_status.eq(1),
                NextState("ASYNC"),
            )
        )

        # ── ASYNC Schedule ──────────────────────────────────────────────

        fsm.act("ASYNC",
            If(~self.async_enable,
                NextState("IDLE"),
            ).Else(
                # Process one async transfer (reclamation model)
                # In real hardware:
                #   1. Read ASYNCLISTADDR to get the circular list head
                #   2. Walk linked QHs, execute one qTD
                #   3. Advance head pointer (doorbell)
                #
                # Set up a transfer request
                self.transfer_request.valid.eq(1),
                # Request fields populated by QH/qTD from memory
                NextState("WAIT_XFER"),
            )
        )

        # ── WAIT_XFER: wait for transfer completion ─────────────────────

        fsm.act("WAIT_XFER",
            If(self.transfer_response.done,
                # Write back status to QH/qTD overlay
                # Advance to next qTD or next QH
                self.transfer_request.valid.eq(0),
                NextState("IDLE"),
            )
        )
