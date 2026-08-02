#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" DECA EHCI Keyboard Host — Terasic DECA SoC with EHCI + VexRiscv.

Instantiates the liteusb EHCI USB host controller on the DECA's ULPI
PHY, adds a VexRiscv CPU, and exposes all 8 user LEDs for firmware-
controlled keycode display.  The firmware (firmware/) performs USB
keyboard enumeration and displays pressed keycodes in binary on the
LEDs.

Usage
-----
    python deca_ehci_host.py --build --load

    # UART debug output:
    litex_term /dev/ttyUSB0
"""

import os

from migen import *

from litex.gen import *
from litex.soc.integration.soc import SoCRegion, SoCCore
from litex.soc.integration.builder import Builder
from litex.soc.interconnect.csr import CSRStorage

from litex_boards.platforms import terasic_deca

from liteusb.gateware.interface.ulpi import ULPIInterface
from liteusb.gateware.usb.usb2.host.ehci import USBHostController

from terasic_deca_common import DecaUSBCrg, deca_main


class DecaEHCIKeyboardSoC(SoCCore):
    """ DECA SoC: EHCI USB host + VexRiscv + keycode LEDs.

    Reuses DecaUSBCrg from terasic_deca_common.py for the DECA's
    clock/PLL/reset architecture (clk60 -> MAX10 PLL -> usb domain,
    ULPI REFCLK on W3, power-on reset).  Instantiates the liteusb
    EHCI host controller on the ULPI bus, maps its Wishbone slave
    into the SoC address space, routes its interrupt, and adds an
    8-bit CSR register driving the user LEDs.
    """

    def __init__(self, sys_clk_freq=50e6,
                 sys_from_usb=False,
                 with_por=True,
                 **kwargs):

        # CPU + debug console
        kwargs.setdefault("cpu_type", "vexriscv")
        kwargs.setdefault("uart_name", "jtag_uart")

        self.platform = platform = terasic_deca.Platform()

        # ── Clocks / reset (reuses the DECA USB clock architecture) ─────
        usb_ctrl  = platform.request("usb", 0)
        clk60     = platform.request("clk60", 0)
        ulpi_plat = platform.request("ulpi", 0)

        self.crg = DecaUSBCrg(platform, sys_clk_freq,
            ulpi=ulpi_plat, clk60=clk60,
            sys_from_usb=sys_from_usb, with_por=with_por)

        SoCCore.__init__(self, platform, sys_clk_freq,
            ident="DECA EHCI Keyboard Host", **kwargs)

        # ── ULPI hookup (same adaptation as DecaUSBSoC) ─────────────────
        self.comb += usb_ctrl.cs.eq(1)

        ulpi = ULPIInterface()
        self.comb += [
            ulpi.dir.eq(ulpi_plat.dir),
            ulpi.nxt.eq(ulpi_plat.nxt),
            ulpi_plat.stp.eq(ulpi.stp),
            ulpi_plat.reset_n.eq(~ulpi.rst),
        ]
        self.specials += ulpi.data.get_tristate(ulpi_plat.data)

        # ── EHCI USB Host Controller ────────────────────────────────────
        self.submodules.usb_host = host = USBHostController(
            bus=ulpi,
            handle_clocking=False,   # clocking handled by DecaUSBCrg
            num_ports=1,
        )

        # Wishbone slave: EHCI operational registers at 0xe0000000
        self.bus.add_slave("usb_ehci", host.bus,
            region=SoCRegion(origin=0xe0000000, size=0x1000, cached=False))

        # Interrupt routing
        self.comb += self.cpu.interrupt[17].eq(host.interrupt)

        # ── LED GPIO: all 8 user LEDs (active-low on DECA) ──────────────
        self.submodules.led_out = led_out = CSRStorage(
            size=8, name="led_out",
            description="Keycode LED output (bit 0 = LED0, active-low)")

        for i in range(8):
            self.comb += platform.request("user_led", i).eq(
                ~led_out.storage[i])


def main():
    deca_main(DecaEHCIKeyboardSoC, "EHCI USB Keyboard Host on Terasic DECA")


if __name__ == "__main__":
    main()
