#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" EHCI Operational Register file with Wishbone interface.

Provides the standard EHCI register map: USBCMD, USBSTS, USBINTR, FRINDEX,
CTRLDSSEGMENT, PERIODICLISTBASE, ASYNCLISTADDR, CONFIGFLAG, and PORTSC.

The Wishbone slave interface allows a CPU (or LiteX SoC) to read/write
registers using 32-bit word addressing.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStorage, CSRStatus

from .data_structures import EHCIRegisters as REG


class EHCIRegisterFile(Module):
    """ EHCI-compatible operational register file.

    Provides a Wishbone slave interface at the operational register
    base address for 32-bit register read/write access.

    Parameters
    ----------
    num_ports : int
        Number of downstream ports (port status registers).
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

        # From PORTSC
        self.port_owner         = Signal()

        # ── Register inputs (status written by hardware) ────────────────

        # USBSTS
        self.usb_interrupt      = Signal()
        self.usb_error          = Signal()
        self.port_change_detect = Signal()
        self.frame_list_rollover = Signal()
        self.host_system_error  = Signal()
        self.interrupt_on_aa_ack = Signal()
        self.hc_halted          = Signal(reset=1)

        # FRINDEX
        self.frame_index_in     = Signal(14)

        # PORTSC
        self.port_status        = Signal(32)  # Per-port status bits

        # ── Interrupt output ────────────────────────────────────────────

        self.interrupt          = Signal()

    def do_finalize(self):
        # ── Register storage ────────────────────────────────────────────

        # Capability registers (read-only, hardcoded)
        CAPLENGTH   = 0x20       # 32 bytes of capability registers
        HCIVERSION  = 0x0100     # EHCI 1.0

        # HCSPARAMS — Structural Parameters
        # [31:20] Reserved
        # [19:16] Debug Port Number (0 = none)
        # [15:12] Port Indicators (0 = none)
        # [11:8]  Number of Companion Controllers (1 = integrated TT)
        # [7]     Port Routing Rules (1 = routing rules apply)
        # [6:4]   Reserved
        # [3:0]   Number of Ports
        HCSPARAMS   = (self.num_ports & 0xF) | (1 << 7) | (1 << 8)

        # HCCPARAMS — Capability Parameters
        # [31:16] Reserved
        # [15:8]  Reserved
        # [7]     Reserved
        # [6:4]   Reserved
        # [3:2]   Reserved
        # [1]     Async Schedule Park Capability
        # [0]     64-bit Addressing Capability
        HCCPARAMS   = 0x0000

        # ── Internal register state ─────────────────────────────────────

        # USBCMD
        usbcmd          = Signal(32, reset=0)
        # USBSTS (write-1-to-clear for status bits)
        usbsts          = Signal(32, reset=0)
        # USBINTR
        usbintr         = Signal(32, reset=0)
        # FRINDEX
        frindex         = Signal(32, reset=0)
        # CTRLDSSEGMENT
        ctrldssegment   = Signal(32, reset=0)
        periodiclistbase = Signal(32, reset=0)
        asynclistaddr   = Signal(32, reset=0)
        configflag      = Signal(32, reset=0)
        portsc          = Signal(32, reset=0)

        # ── Register write logic ────────────────────────────────────────

        # Wishbone accesses are synchronous (single driver for bus.ack).
        self.sync.sys += [
            self.bus.ack.eq(0),
        ]

        # Combinatorial read data
        read_data = Signal(32)

        self.comb += [
            If(self.bus.stb & self.bus.cyc & ~self.bus.we,
                Case(self.bus.adr[0:8], {
                    0x00 // 4: read_data.eq(usbcmd),
                    0x04 // 4: read_data.eq(usbsts),
                    0x08 // 4: read_data.eq(usbintr),
                    0x0C // 4: read_data.eq(frindex),
                    0x10 // 4: read_data.eq(ctrldssegment),
                    0x14 // 4: read_data.eq(periodiclistbase),
                    0x18 // 4: read_data.eq(asynclistaddr),
                    0x40 // 4: read_data.eq(configflag),
                    0x44 // 4: read_data.eq(portsc),
                    "default": read_data.eq(0),
                }),
                self.bus.dat_r.eq(read_data),
            )
        ]

        # Write handling + read ack (same synchronous block)
        self.sync.sys += [
            If(self.bus.stb & self.bus.cyc & ~self.bus.we,
                self.bus.ack.eq(1),
            ).Elif(self.bus.stb & self.bus.cyc & self.bus.we,
                Case(self.bus.adr[0:8], {
                    0x00 // 4: usbcmd.eq(self.bus.dat_w),
                    0x08 // 4: usbintr.eq(self.bus.dat_w & REG.USBINTR_IAA |
                                                           REG.USBINTR_HSE |
                                                           REG.USBINTR_FLR |
                                                           REG.USBINTR_PCD |
                                                           REG.USBINTR_ERRINT |
                                                           REG.USBINTR_USBINT),
                    0x0C // 4: frindex.eq(self.bus.dat_w),
                    0x14 // 4: periodiclistbase.eq(self.bus.dat_w & 0xFFFFF000),
                    0x18 // 4: asynclistaddr.eq(self.bus.dat_w & 0xFFFFFFE0),
                    0x40 // 4: configflag.eq(self.bus.dat_w & 0x00000001),
                    0x44 // 4: portsc.eq(self.bus.dat_w),
                }),
                self.bus.ack.eq(1),
            )
        ]

        # ── Derived control signals ─────────────────────────────────────

        self.comb += [
            self.run                .eq(usbcmd[0]),
            self.hc_reset           .eq(usbcmd[1]),
            self.periodic_enable    .eq(usbcmd[4]),
            self.async_enable       .eq(usbcmd[5]),
            self.interrupt_on_aa    .eq(usbcmd[6]),
            self.frame_list_base    .eq(periodiclistbase),
            self.async_list_addr    .eq(asynclistaddr),
            self.configure_flag     .eq(configflag[0]),
            self.port_owner         .eq(portsc[13]),
        ]

        # ── Status update logic ─────────────────────────────────────────

        # USBSTS is a synchronous register combining hardware status
        # inputs with write-1-to-clear semantics (single driver).
        hw_status = Cat(
            self.usb_interrupt,            # bit 0
            self.usb_error,                # bit 1
            self.port_change_detect,       # bit 2
            self.frame_list_rollover,      # bit 3
            self.host_system_error,        # bit 4
            self.interrupt_on_aa_ack,      # bit 5
            Replicate(0, 6),               # bits 6-11 reserved
            self.hc_halted,                # bit 12
            Signal(),                      # bit 13 — reclamation
            Signal(),                      # bit 14 — periodic status
            Signal(),                      # bit 15 — async status
            Replicate(0, 16),              # bits 16-31 reserved
        )

        self.sync.sys += [
            If((self.bus.stb & self.bus.cyc & self.bus.we) &
               (self.bus.adr[0:8] == (REG.OFF_USBSTS // 4)),
                # Software write-1-to-clear
                usbsts.eq(usbsts & ~self.bus.dat_w),
            ).Else(
                # Hardware status follows inputs
                usbsts.eq(hw_status),
            )
        ]

        # ── Interrupt generation ────────────────────────────────────────

        self.comb += [
            self.interrupt.eq(
                (usbsts[0] & usbintr[0]) |   # USB Interrupt
                (usbsts[1] & usbintr[1]) |   # USB Error
                (usbsts[2] & usbintr[2]) |   # Port Change Detect
                (usbsts[3] & usbintr[3]) |   # Frame List Rollover
                (usbsts[4] & usbintr[4]) |   # Host System Error
                (usbsts[5] & usbintr[5])     # Interrupt on Async Advance
            )
        ]
