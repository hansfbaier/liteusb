#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI Capability + Operational register file with Wishbone interface.

Implements the register map required by the EHCI specification Rev 1.0,
Section 2, and expected by the Linux kernel EHCI driver (drivers/usb/host/
ehci.h + ehci_def.h):

    Capability registers at offset 0x00:
        0x00  CAPLENGTH (bits 7:0) | HCIVERSION (bits 31:16)
        0x04  HCSPARAMS
        0x08  HCCPARAMS

    Operational registers at offset CAPLENGTH (= 0x20):
        0x20  USBCMD
        0x24  USBSTS        (bits 0-5 RW1C, bits 12-15 read-only live)
        0x28  USBINTR
        0x2C  FRINDEX
        0x30  CTRLDSSEGMENT
        0x34  PERIODICLISTBASE
        0x38  ASYNCLISTADDR
        0x60  CONFIGFLAG
        0x64  PORTSC[0] (+4 per port)

The Wishbone slave presents 32-bit word-addressed access (bus.adr is the
byte address divided by 4), matching how LiteX maps Wishbone slaves.
"""

from migen import *

from litex.soc.interconnect import wishbone

from .data_structures import EHCIRegisters as REG


# Capability-register constants
_CAPLENGTH  = 0x20       # 32 bytes of capability registers
_HCIVERSION = 0x0100     # EHCI revision 1.0


class EHCIRegisterFile(Module):
    """ EHCI-compatible capability + operational register file.

    Parameters
    ----------
    num_ports : int
        Number of downstream ports (PORTSC registers).

    Wishbone addressing
    -------------------
    bus.adr is word-indexed (byte offset >> 2), per LiteX Wishbone
    convention.
    """

    def __init__(self, num_ports=1):
        self.num_ports = num_ports

        # ── Wishbone bus ────────────────────────────────────────────────

        self.bus = wishbone.Interface(data_width=32, address_width=8)

        # ── Register outputs (driven by this module) ────────────────────

        # From USBCMD
        self.run                = Signal()
        self.hc_reset           = Signal()
        self.periodic_enable    = Signal()
        self.async_enable       = Signal()
        self.interrupt_on_aa    = Signal()

        # From PERIODICLISTBASE
        self.frame_list_base    = Signal(32)

        # From ASYNCLISTADDR
        self.async_list_addr    = Signal(32)

        # From CONFIGFLAG
        self.configure_flag     = Signal()

        # From PORTSC (per port)
        self.port_reset         = Signal(num_ports)  # PR bits
        self.port_owner         = Signal(num_ports)  # PO bits
        self.port_power         = Signal(num_ports)  # PP bits
        self.port_suspend       = Signal(num_ports)  # SUSP bits
        self.port_force_resume  = Signal(num_ports)  # FPR bits
        self.port_test_mode     = Signal(4 * num_ports)
        self.wake_on_connect    = Signal(num_ports)
        self.wake_on_disconnect = Signal(num_ports)
        self.wake_on_overcurrent = Signal(num_ports)

        # ── Register inputs (status written by hardware) ────────────────

        # USBSTS sticky-event strobes (one cycle pulse sets the RW1C bit)
        self.usb_interrupt      = Signal()
        self.usb_error          = Signal()
        self.port_change_detect = Signal()
        self.frame_list_rollover = Signal()
        self.host_system_error  = Signal()
        self.interrupt_on_aa_ack = Signal()

        # USBSTS live status
        self.hc_halted          = Signal(reset=1)
        self.reclamation        = Signal()
        self.periodic_status    = Signal()
        self.async_status       = Signal()

        # HCRESET completion strobe (clears USBCMD.HCRESET)
        self.hc_reset_done      = Signal()

        # FRINDEX
        self.frame_index_in     = Signal(14)

        # PORTSC live status (per port)
        self.port_connect        = Signal(num_ports)  # CCS (live)
        self.port_connect_change = Signal(num_ports)  # CSC set strobe
        self.port_enable         = Signal(num_ports)  # PE (live-ish; see below)
        self.port_enable_change  = Signal(num_ports)  # PEC set strobe
        self.port_overcurrent    = Signal(num_ports)  # OCA (live)
        self.port_line_status    = Signal(2 * num_ports)

        # ── Interrupt output ────────────────────────────────────────────

        self.interrupt          = Signal()

    def do_finalize(self):
        n = self.num_ports

        # ── Capability registers (read-only, hardcoded) ─────────────────
        #
        # HCSPARAMS — Structural Parameters (EHCI §2.1.3)
        #   [3:0]  N_PORTS
        #   [4]    PPC  (Port Power Control: ports have power switches)
        #   [11:8] N_CC   = 0  (no companion controllers; integrated TT)
        #   [7]    Port Routing Rules = 0 (no port routing rules)
        HCSPARAMS = (n & 0xF) | (1 << 4)

        # HCCPARAMS — Capability Parameters (EHCI §2.1.4)
        #   [0]   64-bit addressing capability = 0
        #   [1]   Programmable Frame List Flag = 0 (fixed 1024 entries)
        #   [2]   Asynchronous Schedule Park Capability = 0
        HCCPARAMS = 0x0000

        # ── Internal register state ─────────────────────────────────────

        # USBCMD fields
        cmd_run      = Signal()
        cmd_hcreset  = Signal()
        cmd_fls      = Signal(2)
        cmd_pse      = Signal()
        cmd_ase      = Signal()
        cmd_iaa      = Signal()
        cmd_itc      = Signal(8)

        # USBSTS sticky (RW1C) bits 0-5
        sts_sticky   = Signal(6)

        # USBINTR
        usbintr      = Signal(6)

        # FRINDEX (14 bits)
        frindex      = Signal(14)

        # Pointer / config registers
        ctrldssegment    = Signal(32)
        periodiclistbase = Signal(32)
        asynclistaddr    = Signal(32)
        configflag       = Signal()

        # PORTSC per-port state
        ps_csc   = Signal(n)            # RW1C
        ps_pec   = Signal(n)            # RW1C
        ps_occ   = Signal(n)            # RW1C
        ps_pe    = Signal(n)            # PE: HW-set, SW/HW-clear
        ps_fpr   = Signal(n)
        ps_susp  = Signal(n)
        ps_pr    = Signal(n)
        ps_pp    = Signal(n, reset=(1 << n) - 1)  # ports powered at boot
        ps_po    = Signal(n)
        ps_pic   = Signal(2 * n)
        ps_ptc   = Signal(4 * n)
        ps_wkoc  = Signal(n)
        ps_wkdsc = Signal(n)
        ps_wkc   = Signal(n)

        # ── Address decode ──────────────────────────────────────────────
        #
        # bus.adr is word-indexed: word = byte_offset >> 2.

        OPBASE_W   = _CAPLENGTH // 4             # 0x08
        USBCMD_W   = OPBASE_W + 0x00 // 4        # 0x08
        USBSTS_W   = OPBASE_W + 0x04 // 4        # 0x09
        USBINTR_W  = OPBASE_W + 0x08 // 4        # 0x0A
        FRINDEX_W  = OPBASE_W + 0x0C // 4        # 0x0B
        CTRLDS_W   = OPBASE_W + 0x10 // 4        # 0x0C
        PLBASE_W   = OPBASE_W + 0x14 // 4        # 0x0D
        ASYNC_W    = OPBASE_W + 0x18 // 4        # 0x0E
        CFGFLAG_W  = OPBASE_W + 0x40 // 4        # 0x18
        PORTSC_W   = OPBASE_W + 0x44 // 4        # 0x19

        adr = self.bus.adr[0:8]
        req = self.bus.stb & self.bus.cyc

        # ── Read path (combinatorial data, ack in sync block) ───────────

        usbcmd_read = Signal(32)
        usbsts_read = Signal(32)
        portsc_read = [Signal(32) for _ in range(n)]

        # USBCMD layout: [0]RUN [1]HCRESET [3:2]FLS [4]PSE [5]ASE [6]IAA
        #                [7]LHCR [10:8]ASP [11]ASPME [23:16]ITC
        self.comb += usbcmd_read.eq(
            cmd_run | (cmd_hcreset << 1) | (cmd_fls << 2) |
            (cmd_pse << 4) | (cmd_ase << 5) | (cmd_iaa << 6) |
            (cmd_itc << 16)
        )

        self.comb += usbsts_read.eq(
            sts_sticky[0:6] |
            (self.hc_halted << 12) |
            (self.reclamation << 13) |
            (self.periodic_status << 14) |
            (self.async_status << 15)
        )

        for i in range(n):
            self.comb += portsc_read[i].eq(
                self.port_connect[i] |            # [0]  CCS
                (ps_csc[i] << 1) |                # [1]  CSC
                (ps_pe[i] << 2) |                 # [2]  PE
                (ps_pec[i] << 3) |                # [3]  PEC
                (self.port_overcurrent[i] << 4) | # [4]  OCA
                (ps_occ[i] << 5) |                # [5]  OCC
                (ps_fpr[i] << 6) |                # [6]  FPR
                (ps_susp[i] << 7) |               # [7]  SUSP
                (ps_pr[i] << 8) |                 # [8]  PR
                # [9] reserved (EHCI has no HSP bit)
                (self.port_line_status[2*i:2*i+2] << 10) |
                (ps_pp[i] << 12) |                # [12] PP
                (ps_po[i] << 13) |                # [13] PO
                (ps_pic[2*i:2*i+2] << 14) |       # [15:14] PIC
                (ps_ptc[4*i:4*i+4] << 16) |       # [19:16] PTC
                (ps_wkc[i] << 20) |               # [20] WKCNNT_E
                (ps_wkdsc[i] << 21) |             # [21] WKDSCNNT_E
                (ps_wkoc[i] << 22)                # [22] WKOC_E
            )

        read_data = Signal(32)
        self.comb += [
            read_data.eq(0),
            If(adr == 0x00 // 4,
                read_data.eq(_CAPLENGTH | (_HCIVERSION << 16)),
            ).Elif(adr == 0x04 // 4,
                read_data.eq(HCSPARAMS),
            ).Elif(adr == 0x08 // 4,
                read_data.eq(HCCPARAMS),
            ).Elif(adr == USBCMD_W,
                read_data.eq(usbcmd_read),
            ).Elif(adr == USBSTS_W,
                read_data.eq(usbsts_read),
            ).Elif(adr == USBINTR_W,
                read_data.eq(usbintr),
            ).Elif(adr == FRINDEX_W,
                read_data.eq(frindex),
            ).Elif(adr == CTRLDS_W,
                read_data.eq(ctrldssegment),
            ).Elif(adr == PLBASE_W,
                read_data.eq(periodiclistbase),
            ).Elif(adr == ASYNC_W,
                read_data.eq(asynclistaddr),
            ).Elif(adr == CFGFLAG_W,
                read_data.eq(configflag),
            )
        ]
        for i in range(n):
            self.comb += If(adr == PORTSC_W + i,
                read_data.eq(portsc_read[i]),
            )

        self.comb += self.bus.dat_r.eq(read_data)

        # ── Write + ack path (synchronous) ──────────────────────────────

        w_data = self.bus.dat_w

        self.sync.sys += [
            self.bus.ack.eq(0),
            If(req & ~self.bus.ack,
                self.bus.ack.eq(1),
            ),
        ]

        # Register writes + hardware status updates.
        #
        # All assignments to a given register bit live in a single sync
        # block (multiple sync blocks driving one signal are a synthesis
        # error). Hardware status sets have priority over software clears.
        wb_write = req & self.bus.we

        self.sync.sys += [
            # ── USBCMD ──
            If(self.hc_reset_done,
                cmd_hcreset.eq(0),
            ).Elif(wb_write & (adr == USBCMD_W) & w_data[1],
                cmd_hcreset.eq(1),
            ),
            If(self.interrupt_on_aa_ack,
                # IAA doorbell auto-clears once the status is reported
                cmd_iaa.eq(0),
            ).Elif(wb_write & (adr == USBCMD_W),
                cmd_iaa.eq(w_data[6]),
            ),
            If(wb_write & (adr == USBCMD_W),
                cmd_run.eq(w_data[0]),
                cmd_fls.eq(w_data[2:4]),
                cmd_pse.eq(w_data[4]),
                cmd_ase.eq(w_data[5]),
                cmd_itc.eq(w_data[16:24]),
            ),

            # ── USBSTS sticky bits: HW set has priority over W1C ──
            If(self.usb_interrupt,      sts_sticky[0].eq(1)),
            If(self.usb_error,          sts_sticky[1].eq(1)),
            If(self.port_change_detect, sts_sticky[2].eq(1)),
            If(self.frame_list_rollover, sts_sticky[3].eq(1)),
            If(self.host_system_error,  sts_sticky[4].eq(1)),
            If(self.interrupt_on_aa_ack, sts_sticky[5].eq(1)),
            If(wb_write & (adr == USBSTS_W),
                If(w_data[0] & ~self.usb_interrupt,       sts_sticky[0].eq(0)),
                If(w_data[1] & ~self.usb_error,           sts_sticky[1].eq(0)),
                If(w_data[2] & ~self.port_change_detect,  sts_sticky[2].eq(0)),
                If(w_data[3] & ~self.frame_list_rollover, sts_sticky[3].eq(0)),
                If(w_data[4] & ~self.host_system_error,   sts_sticky[4].eq(0)),
                If(w_data[5] & ~self.interrupt_on_aa_ack, sts_sticky[5].eq(0)),
            ),

            # ── USBINTR ──
            If(wb_write & (adr == USBINTR_W),
                usbintr.eq(w_data[0:6]),
            ),

            # ── FRINDEX: follows the schedule engine while running;
            #    writable by software only while halted ──
            If(~self.hc_halted,
                frindex.eq(self.frame_index_in),
            ).Elif(wb_write & (adr == FRINDEX_W),
                frindex.eq(w_data[0:14]),
            ),

            # ── Pointer / config registers ──
            If(wb_write & (adr == CTRLDS_W),
                ctrldssegment.eq(w_data),
            ),
            If(wb_write & (adr == PLBASE_W),
                periodiclistbase.eq(w_data & 0xFFFFF000),
            ),
            If(wb_write & (adr == ASYNC_W),
                asynclistaddr.eq(w_data & 0xFFFFFFE0),
            ),
            If(wb_write & (adr == CFGFLAG_W),
                configflag.eq(w_data[0]),
            ),
        ]

        # PORTSC per-port: HW change sets have priority over SW W1C clears
        for i in range(n):
            port_write = wb_write & (adr == PORTSC_W + i)
            self.sync.sys += [
                If(self.port_connect_change[i],
                    ps_csc[i].eq(1),
                ).Elif(port_write & w_data[1],
                    ps_csc[i].eq(0),
                ),
                If(self.port_enable_change[i],
                    ps_pec[i].eq(1),
                ).Elif(port_write & w_data[3],
                    ps_pec[i].eq(0),
                ),
                If(port_write & w_data[5],
                    ps_occ[i].eq(0),
                ),

                # PE: set by HW on successful reset; cleared by SW write of
                # 0 or by disconnect
                If(self.port_enable[i],
                    ps_pe[i].eq(1),
                ).Elif(~self.port_connect[i] | (port_write & ~w_data[2]),
                    ps_pe[i].eq(0),
                ),

                If(port_write,
                    ps_fpr[i].eq(w_data[6]),
                    ps_susp[i].eq(w_data[7]),
                    ps_pr[i].eq(w_data[8]),
                    ps_pp[i].eq(w_data[12]),
                    ps_po[i].eq(w_data[13]),
                    ps_pic[2*i:2*i+2].eq(w_data[14:16]),
                    ps_ptc[4*i:4*i+4].eq(w_data[16:20]),
                    ps_wkc[i].eq(w_data[20]),
                    ps_wkdsc[i].eq(w_data[21]),
                    ps_wkoc[i].eq(w_data[22]),
                ),
            ]

        # ── Derived control signals ─────────────────────────────────────

        self.comb += [
            self.run                .eq(cmd_run),
            self.hc_reset           .eq(cmd_hcreset),
            self.periodic_enable    .eq(cmd_pse),
            self.async_enable       .eq(cmd_ase),
            self.interrupt_on_aa    .eq(cmd_iaa),
            self.frame_list_base    .eq(periodiclistbase),
            self.async_list_addr    .eq(asynclistaddr),
            self.configure_flag     .eq(configflag),
            self.port_reset         .eq(ps_pr),
            self.port_owner         .eq(ps_po),
            self.port_power         .eq(ps_pp),
            self.port_suspend       .eq(ps_susp),
            self.port_force_resume  .eq(ps_fpr),
            self.port_test_mode     .eq(ps_ptc),
            self.wake_on_overcurrent .eq(ps_wkoc),
            self.wake_on_disconnect .eq(ps_wkdsc),
            self.wake_on_connect    .eq(ps_wkc),
        ]

        # ── Interrupt generation (level) ────────────────────────────────

        self.comb += self.interrupt.eq((sts_sticky & usbintr) != 0)
