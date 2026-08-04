#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI schedule traversal engine with DMA.

Walks the EHCI periodic and asynchronous schedules in system memory:
reads Queue Heads (QH) and Queue Element Transfer Descriptors (qTD),
executes the transfers they describe via the USBHostTransferEngine
(chunking each qTD into MaxPacketSize transactions per USB 2.0 §8.5),
and writes back qTD status per EHCI Rev 1.0 §3.5.3/§4.10.

References
----------
EHCI Spec Rev 1.0, Section 3: Data Structures
EHCI Spec Rev 1.0, Section 4: Operational Model

Supported:
  - Asynchronous schedule: QH chains from ASYNCLISTADDR (control, bulk)
  - Periodic schedule: interrupt QHs via the frame list (one qTD chunk
    per microframe per QH)
  - qTD chaining (Next qTD pointer), alternate-free short-packet handling
  - IOC -> USBINT, error -> USBERRINT, IAA doorbell

Not supported (documented in REVIEW.md):
  - Isochronous iTD/siTD, split transactions, FSTN, 64-bit addressing,
    qTD alternate pointer short-read retry, PING
"""

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from litex.soc.interconnect import wishbone

from .. import USBPacketID, USBSpeed
from .data_structures import QueueHeadLayout as QHL, QueueTD as QTD
from .transfer import HostTransferRequest, HostTransferResponse
from ..packet import USBInStreamInterface, USBOutStreamInterface


# qTD PID codes (EHCI §3.5.3, Table 3-16)
_QTD_PID_OUT   = 0
_QTD_PID_IN    = 1
_QTD_PID_SETUP = 2

# QH endpoint speed codes (EHCI §3.6.2)
_EPS_FULL  = 0
_EPS_LOW   = 1
_EPS_HIGH  = 2

# Link pointer type tags (EHCI §3.1)
_TYPE_ITD  = 0
_TYPE_QH   = 1
_TYPE_SITD = 2
_TYPE_FSTN = 3

# Payload staging buffer depth (one chunk never exceeds 1024 bytes:
# HS bulk MaxPacket is 512, HS control 64, FS 64, LS 8)
_BUF_DEPTH = 1024


class EHCIScheduleProcessor(Module):
    """ Traverses EHCI schedule data structures and executes transfers.

    Parameters
    ----------
    num_ports : int
        Number of downstream ports.

    Interface
    ---------
    mem : wishbone.Interface
        Bus master used to read/write the EHCI data structures and the
        transfer payload buffers in system memory.
    transfer_request / transfer_response : HostTransfer*
        Handshake with the USBHostTransferEngine.
    tx_stream : USBInStreamInterface
        Payload bytes for OUT/SETUP chunks (drives the transfer engine).
    rx_stream : USBOutStreamInterface
        Payload bytes received during IN chunks (from the transfer engine).
    usbint / usberr / iaa : Signal() outputs
        Status strobes for the register file (USBSTS bits 0/1/5).
    interrupt_on_aa : Signal() input
        IAA doorbell from USBCMD.
    """

    def __init__(self, num_ports=1):
        # ── Control inputs ──────────────────────────────────────────────

        self.run                 = Signal()      # USBCMD.RUN
        self.periodic_enable     = Signal()      # USBCMD.PSE
        self.async_enable        = Signal()      # USBCMD.ASE
        self.frame_list_base     = Signal(32)    # PERIODICLISTBASE
        self.async_list_addr     = Signal(32)    # ASYNCLISTADDR
        self.interrupt_on_aa     = Signal()      # USBCMD.IAA

        # ── Status outputs ──────────────────────────────────────────────

        self.frame_index         = Signal(14)    # FRINDEX [13:3] = entry
        self.sof_strobe          = Signal()      # New microframe
        self.hc_halted           = Signal(reset=1)
        self.periodic_status     = Signal()
        self.async_status        = Signal()

        # Status strobes to the register file
        self.usbint              = Signal()
        self.usberr              = Signal()
        self.iaa                 = Signal()

        # ── Bus master ──────────────────────────────────────────────────

        self.mem                 = wishbone.Interface(data_width=32)

        # ── Transfer interface ──────────────────────────────────────────

        self.transfer_request  = HostTransferRequest()
        self.transfer_response = HostTransferResponse()

        # Payload streams (to/from the transfer engine)
        self.tx_stream = USBInStreamInterface()
        self.rx_stream = USBOutStreamInterface()

        self._num_ports = num_ports

    def do_finalize(self):
        # ── Latched schedule state ──────────────────────────────────────

        qh_addr      = Signal(32)   # current QH (32-byte aligned)
        qh_hlp       = Signal(32)   # its horizontal link pointer
        ep_char      = Signal(32)   # endpoint characteristics dword
        qtd_addr     = Signal(32)   # current qTD (32-byte aligned)
        qtd_next     = Signal(32)   # its next-qTD pointer
        token        = Signal(32)   # its token dword
        buf_addr     = Signal(32)   # current payload byte address
        buf_page_idx = Signal(3)    # current buffer page (0-4)
        remaining    = Signal(16)   # bytes left in this qTD
        chunk        = Signal(11)   # bytes in this transaction
        toggle       = Signal()     # data toggle
        ioc          = Signal()
        is_periodic  = Signal()     # walking the periodic list
        short_packet = Signal()     # IN chunk came up short
        rx_len       = Signal(11)   # bytes received in this chunk

        # Decoded qTD/QH fields
        max_packet  = ep_char[16:27]   # QH EP_CHAR [26:16]
        dev_address = ep_char[0:7]     # [6:0]
        endpoint    = ep_char[8:12]    # [11:8]
        eps         = ep_char[12:14]   # [13:12]

        # ── Payload staging buffer (32-bit words, 1 KiB total) ─────────

        _BUF_WORDS = _BUF_DEPTH // 4
        buf_ram = Memory(32, _BUF_WORDS)
        self.specials += buf_ram
        buf_write = buf_ram.get_port(write_capable=True, clock_domain="usb")
        # Async (combinational) read: the TX path picks a byte lane of the
        # current word every cycle — a registered port would be one word
        # behind at page boundaries.
        buf_read  = buf_ram.get_port(async_read=True, clock_domain="usb")
        self.specials += buf_write, buf_read

        mem_wait    = Signal()   # 0 = address just presented, 1 = awaiting ack
        fail_stall  = Signal()   # latched at transfer completion
        fail_babble = Signal()
        rx_flush    = Signal()   # flush trailing partial IN word
        tx_count    = Signal(max=_BUF_DEPTH)
        rx_count    = Signal(max=_BUF_DEPTH)
        rx_assembly = Signal(max=_BUF_DEPTH)
        rx_word     = Signal(32)
        rx_word_next = Signal(32)
        rx_word_we  = Signal()
        tx_word     = Signal(32)

        # ── Bus master defaults ─────────────────────────────────────────

        self.comb += [
            self.mem.adr.eq(0),
            self.mem.dat_w.eq(0),
            self.mem.sel.eq(0xF),
            self.mem.cyc.eq(0),
            self.mem.stb.eq(0),
            self.mem.we.eq(0),
        ]

        # ── Frame index (FRINDEX semantics: +1 per microframe) ──────────

        self.sync.usb += [
            If(self.sof_strobe & self.run,
                self.frame_index.eq(self.frame_index + 1),
            ),
            self.hc_halted.eq(~self.run),
        ]

        # ── Status outputs (level, per section) ─────────────────────────

        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.sched_fsm = fsm

        self.comb += [
            self.periodic_status.eq(is_periodic & self.run),
            self.async_status.eq(~is_periodic & self.run &
                                 self.async_enable & ~fsm.ongoing("IDLE")),
            self.usbint.eq(0),
            self.usberr.eq(0),
            self.iaa.eq(0),
        ]

        # ── Transfer request defaults ───────────────────────────────────

        self.comb += [
            self.transfer_request.valid.eq(0),
            # qTD PID code (0=OUT, 1=IN, 2=SETUP) → USB token PID
            If(token[8:10] == _QTD_PID_IN,
                self.transfer_request.pid.eq(USBPacketID.IN),
            ).Elif(token[8:10] == _QTD_PID_SETUP,
                self.transfer_request.pid.eq(USBPacketID.SETUP),
            ).Else(
                self.transfer_request.pid.eq(USBPacketID.OUT),
            ),
            self.transfer_request.address.eq(dev_address),
            self.transfer_request.endpoint.eq(endpoint),
            self.transfer_request.data_toggle.eq(toggle),
            self.transfer_request.length.eq(chunk),
            self.transfer_request.max_packet.eq(max_packet),
            self.transfer_request.cerr.eq(token[10:12]),
            self.transfer_request.ioc.eq(ioc),
            If(eps == _EPS_HIGH,
                self.transfer_request.speed.eq(USBSpeed.HIGH),
            ).Elif(eps == _EPS_LOW,
                self.transfer_request.speed.eq(USBSpeed.LOW),
            ).Else(
                self.transfer_request.speed.eq(USBSpeed.FULL),
            ),
        ]

        # ── Helper: wishbone read/write cycles ──────────────────────────
        # (each state asserts cyc+stb until ack, then moves on)

        # ── IDLE ────────────────────────────────────────────────────────

        fsm.act("IDLE",
            NextValue(is_periodic, 0),
            If(self.run & self.sof_strobe & self.periodic_enable,
                NextValue(is_periodic, 1),
                # Frame list entry address: base + FRINDEX[12:3] * 4
                NextState("PERIODIC_READ_ENTRY"),
            ).Elif(self.run & self.async_enable,
                If(self.async_list_addr[0],
                    # T bit set: empty async list
                    NextState("IDLE"),
                ).Else(
                    NextValue(qh_addr, Cat(Constant(0, 5), self.async_list_addr[5:32])),
                    NextState("QH_READ_HLP"),
                )
            )
        )

        # ── Periodic list ───────────────────────────────────────────────

        fsm.act("PERIODIC_READ_ENTRY",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((self.frame_list_base + (self.frame_index[3:13] << 2))[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                If(self.mem.dat_r[0],
                    # T=1: empty slot
                    NextState("IDLE"),
                ).Elif(self.mem.dat_r[1:3] == _TYPE_QH,
                    NextValue(qh_addr, self.mem.dat_r & 0xFFFFFFE0),
                    NextState("QH_READ_HLP"),
                ).Else(
                    # iTD/siTD/FSTN: not supported
                    NextState("IDLE"),
                )
            )
        )

        # ── QH processing (shared by async + periodic) ──────────────────

        fsm.act("QH_READ_HLP",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qh_addr + 4 * QHL.OFF_HLP)[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(qh_hlp, self.mem.dat_r),
                NextState("QH_READ_EP_CHAR"),
            )
        )

        fsm.act("QH_READ_EP_CHAR",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qh_addr + 4 * QHL.OFF_EP_CHAR)[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(ep_char, self.mem.dat_r),
                NextState("QH_READ_CUR_QTD"),
            )
        )

        fsm.act("QH_READ_CUR_QTD",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qh_addr + 4 * QHL.OFF_CUR_QTD)[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                If(self.mem.dat_r == 0,
                    # No current qTD: next QH
                    NextState("QH_NEXT"),
                ).Else(
                    NextValue(qtd_addr, self.mem.dat_r),
                    NextState("QTD_READ_NEXT"),
                )
            )
        )

        # ── qTD processing ──────────────────────────────────────────────

        fsm.act("QTD_READ_NEXT",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qtd_addr + 4 * QTD.OFF_NEXT_QTD)[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(qtd_next, self.mem.dat_r),
                NextState("QTD_READ_TOKEN"),
            )
        )

        fsm.act("QTD_READ_TOKEN",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qtd_addr + 4 * QTD.OFF_TOKEN)[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(token, self.mem.dat_r),
                If(~self.mem.dat_r[7],
                    # Not Active: skip to the next qTD
                    NextState("QTD_SKIP"),
                ).Else(
                    NextValue(toggle, self.mem.dat_r[31]),
                    NextValue(remaining, self.mem.dat_r[16:31]),
                    NextValue(ioc, self.mem.dat_r[15]),
                    NextValue(buf_page_idx, self.mem.dat_r[12:15]),
                    NextState("QTD_READ_BUFFER"),
                )
            )
        )

        # Read the buffer pointer for the current page (C_Page at entry,
        # then advances as pages are crossed)
        fsm.act("QTD_READ_BUFFER",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qtd_addr + 4 * (QTD.OFF_BUF0 + buf_page_idx))[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(buf_addr,
                    (self.mem.dat_r & 0xFFFFF000) | self.mem.dat_r[0:12]),
                NextState("CHUNK_SETUP"),
            )
        )

        # Re-read the buffer pointer when a page boundary is crossed
        fsm.act("QTD_READ_NEXT_BUFFER",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((qtd_addr + 4 * (QTD.OFF_BUF0 + buf_page_idx))[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(buf_addr, self.mem.dat_r & 0xFFFFF000),
                NextState("CHUNK_SETUP"),
            )
        )

        # ── Transaction chunking ────────────────────────────────────────

        fsm.act("CHUNK_SETUP",
            If(remaining == 0,
                # Zero-length packet
                NextValue(chunk, 0),
                NextState("XFER_START"),
            ).Elif(remaining > max_packet,
                If(max_packet > _BUF_DEPTH,
                    NextValue(chunk, _BUF_DEPTH),
                ).Else(
                    NextValue(chunk, max_packet),
                ),
                NextState("PREPARE_OUT"),
            ).Else(
                NextValue(chunk, remaining[0:11]),
                NextState("PREPARE_OUT"),
            )
        )

        # OUT/SETUP: DMA the chunk payload into the staging buffer
        fsm.act("PREPARE_OUT",
            If(token[8:10] == _QTD_PID_IN,
                NextValue(rx_count, 0),
                NextState("XFER_START"),
            ).Elif(chunk == 0,
                NextState("XFER_START"),
            ).Else(
                NextValue(tx_count, 0),
                NextValue(rx_count, 0),
                NextState("DMA_READ"),
            )
        )

        fsm.act("DMA_READ",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.adr.eq((buf_addr + (tx_count << 2))[2:32]),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(tx_count, tx_count + 1),
                If((tx_count + 1) >= ((chunk + 3) >> 2),
                    NextValue(tx_count, 0),
                    NextState("XFER_START"),
                )
            )
        )

        # DMA read data lands in the staging buffer (whole words)
        self.comb += [
            If(fsm.ongoing("DMA_READ"),
                buf_write.adr.eq(tx_count),
                buf_write.dat_w.eq(self.mem.dat_r),
                buf_write.we.eq(self.mem.ack),
            ).Elif(fsm.ongoing("XFER_WAIT") | fsm.ongoing("XFER_START"),
                # RX byte assembly for IN transfers (+ trailing flush)
                buf_write.adr.eq(rx_assembly[2:]),
                buf_write.dat_w.eq(Mux(rx_flush, rx_word, rx_word_next)),
                buf_write.we.eq(rx_word_we | rx_flush),
            ).Else(
                buf_write.we.eq(0),
            )
        ]

        # ── Transfer execution ──────────────────────────────────────────

        fsm.act("XFER_START",
            self.transfer_request.valid.eq(1),
            NextValue(tx_count, 0),
            NextValue(rx_count, 0),
            NextValue(rx_assembly, 0),
            NextValue(rx_word, 0),
            NextState("XFER_WAIT"),
        )

        # Feed payload bytes from the staging buffer to the engine:
        # word from the read port, byte lane picked by tx_count[0:2]
        self.comb += [
            buf_read.adr.eq(tx_count[2:]),
            tx_word.eq(buf_read.dat_r),
            self.tx_stream.payload.eq(
                Array(tx_word[i*8:(i+1)*8] for i in range(4))[tx_count[0:2]]),
            self.tx_stream.valid.eq(
                fsm.ongoing("XFER_WAIT") & (tx_count < chunk) &
                (token[8:10] != _QTD_PID_IN)),
        ]

        # RX byte assembly (IN): pack bytes little-endian into words
        self.comb += [
            rx_word_we.eq(0),
            rx_word_next.eq(rx_word |
                (self.rx_stream.payload << (rx_assembly[0:2] * 8))),
            If(fsm.ongoing("XFER_WAIT") & self.rx_stream.valid &
               self.rx_stream.next & (rx_assembly[0:2] == 3),
                rx_word_we.eq(1),
            ),
        ]

        # Flush a trailing partial word when an IN transfer completes:
        # write the assembled lanes as-is (no new payload merged)
        self.comb += rx_flush.eq(
            fsm.ongoing("XFER_WAIT") & self.transfer_response.done &
            (rx_assembly[0:2] != 0))

        self.sync.usb += [
            If(fsm.ongoing("XFER_WAIT") & self.tx_stream.valid &
               self.tx_stream.ready,
                tx_count.eq(tx_count + 1),
            ),
            If(fsm.ongoing("XFER_WAIT") & self.rx_stream.valid &
               self.rx_stream.next,
                rx_count.eq(rx_count + 1),
                rx_assembly.eq(rx_assembly + 1),
                If(rx_assembly[0:2] == 3,
                    rx_word.eq(0),
                ).Else(
                    rx_word.eq(rx_word_next),
                )
            ),
        ]

        fsm.act("XFER_WAIT",
            If(self.transfer_response.done,
                NextValue(rx_len, self.transfer_response.bytes_xfer),
                NextValue(fail_stall, self.transfer_response.stall),
                NextValue(fail_babble, self.transfer_response.babble),
                If(self.transfer_response.ack,
                    If(token[8:10] == _QTD_PID_IN,
                        NextValue(rx_count, 0),
                        NextState("DMA_WRITE"),
                    ).Else(
                        NextValue(rx_len, chunk),
                        NextState("CHUNK_DONE"),
                    )
                ).Elif(self.transfer_response.nak,
                    # Retired without error: the qTD stays active, no
                    # writeback — retried on the next pass (EHCI §4.10)
                    NextState("QH_NEXT"),
                ).Else(
                    # stall / nyet / error / babble
                    NextState("QTD_FAIL"),
                )
            )
        )

        # IN: write the received words back to memory.
        # (Full words only: a trailing partial word is written whole,
        # which may touch up to 3 bytes past the transfer — buffer
        # layout must tolerate this; see REVIEW.md.)
        fsm.act("DMA_WRITE",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.we.eq(1),
            self.mem.adr.eq((buf_addr + (rx_count << 2))[2:32]),
            self.mem.dat_w.eq(buf_read.dat_r),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextValue(rx_count, rx_count + 1),
                If((rx_count + 1) >= ((rx_len + 3) >> 2),
                    NextState("CHUNK_DONE"),
                )
            )
        )
        self.comb += [
            If(fsm.ongoing("DMA_WRITE"),
                buf_read.adr.eq(rx_count),
            )
        ]

        fsm.act("CHUNK_DONE",
            # short IN packet terminates the qTD early
            If((token[8:10] == _QTD_PID_IN) & (rx_len < chunk),
                NextValue(remaining, 0),
                NextValue(short_packet, 1),
                NextState("QTD_COMPLETE"),
            ).Else(
                NextValue(remaining, remaining - chunk),
                NextValue(toggle, ~toggle),
                NextValue(buf_addr, buf_addr + chunk),
                If(remaining == chunk,
                    NextState("QTD_COMPLETE"),
                ).Elif(((buf_addr + chunk) & 0xFFF) == 0,
                    # crossed a page boundary: fetch the next page pointer
                    NextValue(buf_page_idx, buf_page_idx + 1),
                    NextState("QTD_READ_NEXT_BUFFER"),
                ).Else(
                    NextState("CHUNK_SETUP"),
                )
            )
        )

        # ── qTD completion / writeback ──────────────────────────────────

        fsm.act("QTD_COMPLETE",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.we.eq(1),
            self.mem.adr.eq((qtd_addr + 4 * QTD.OFF_TOKEN)[2:32]),
            self.mem.dat_w.eq(
                (toggle << 31) |
                (remaining << 16) |
                (ioc << 15) |
                (buf_page_idx << 12) |
                (token[10:12] << 10) |
                (token[8:10] << 8)
                # status = 0: Active cleared, no errors
            ),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextState("QTD_ADVANCE"),
            )
        )

        fsm.act("QTD_FAIL",
            self.mem.cyc.eq(1),
            self.mem.stb.eq(1),
            self.mem.we.eq(1),
            self.mem.adr.eq((qtd_addr + 4 * QTD.OFF_TOKEN)[2:32]),
            self.mem.dat_w.eq(
                (toggle << 31) |
                (remaining << 16) |
                (ioc << 15) |
                (buf_page_idx << 12) |
                (token[10:12] << 10) |
                (token[8:10] << 8) |
                QTD.TOKEN_HALTED |
                Mux(fail_stall, 0, QTD.TOKEN_XACTERR) |
                Mux(fail_babble, QTD.TOKEN_BABBLE, 0)
            ),
            If(~mem_wait,
                NextValue(mem_wait, 1),
            ).Elif(self.mem.ack,
                NextValue(mem_wait, 0),
                NextState("QH_NEXT"),
            )
        )

        fsm.act("QTD_ADVANCE",
            NextValue(qtd_addr, qtd_next & 0xFFFFFFE0),
            If(qtd_next[0],
                # T=1: this QH is done
                NextState("QH_NEXT"),
            ).Else(
                NextState("QTD_READ_NEXT"),
            )
        )

        fsm.act("QTD_SKIP",
            NextValue(qtd_addr, qtd_next & 0xFFFFFFE0),
            If(qtd_next[0],
                NextState("QH_NEXT"),
            ).Else(
                NextState("QTD_READ_NEXT"),
            )
        )

        # ── Advance to the next QH via the horizontal link pointer ──────

        fsm.act("QH_NEXT",
            If(qh_hlp[0],
                # T=1: end of the list
                NextState("IDLE"),
            ).Elif(is_periodic & (qh_hlp[1:3] != _TYPE_QH),
                # Periodic lists may hold non-QH entries: stop here
                NextState("IDLE"),
            ).Else(
                NextValue(qh_addr, qh_hlp & 0xFFFFFFE0),
                NextState("QH_READ_HLP"),
            )
        )

        # ── Status strobes ──────────────────────────────────────────────
        # USBINT on IOC completion, USBERRINT on failure, IAA doorbell.

        self.comb += [
            If(fsm.ongoing("QTD_COMPLETE") & self.mem.ack & ioc,
                self.usbint.eq(1),
            ),
            If(fsm.ongoing("QTD_COMPLETE") & self.mem.ack &
               self.interrupt_on_aa & ~is_periodic,
                self.iaa.eq(1),
            ),
            If(fsm.ongoing("QTD_FAIL") & self.mem.ack,
                self.usberr.eq(1),
            ),
        ]
