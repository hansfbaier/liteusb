#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Shared Terasic DECA hardware support for LiteUSB examples.

Factored-out, hardware-specific glue so every example can be built as a
DECA bitstream with minimal code:

- ``DecaUSBCrg``    — Single-PLL clock/reset generator
- ``DecaUSBSoC``    — SoCCore base: ULPI hookup, status/diagnostic LEDs
- ``deca_main``     — shared command line (build/load, --debug-leds)

Clock architecture — mirrors the original Amaranth/LUNA DECA design:
  https://github.com/amaranth-farm/deca-usb2-audio-interface
  (see gateware/arrow_deca.py, class ArrowDECAClockAndResetController)

  TUSB1210 PHY → clk60 (H11, input) → MAX10 PLL → cd_usb (60MHz, -120°)
               ulpi.clk (W3, REFCLK to PHY) ←─── same PLL output

  Single PLL from clk60 drives both the internal usb/sync domains AND
  the ULPI REFCLK (W3) — one PLL, one clock net, no separate REFCLK
  domain.  The original design uses an ALTPLL Instance directly; the
  Max10PLL wrapper here produces the same single-PLL structure.

NOTE: the usb clock is created with ``with_reset=False``. The PHY only
produces its 60MHz CLOCK output once it sees REFCLK; gating the usb domain
reset on PLL lock would hold the PHY in reset and deadlock the clock loop.
The PLL free-runs at power-on, driving REFCLK to bootstrap the PHY.

Usage (in an example):

    from terasic_deca_common import DecaUSBSoC, deca_main

    class _DecaSoC(DecaUSBSoC):
        def add_usb_device(self, ulpi):
            self.submodules.dev = MyDevice(ulpi, handle_clocking=False)
            self.usb = self.dev.usb

    deca_main(_DecaSoC, "My Device on Terasic DECA")
"""

import os

from migen import *

from litex.gen import *
from litex.soc.cores.clock import Max10PLL
from litex.soc.integration.soc import *
from litex.soc.integration.builder import *

from litex_boards.platforms import terasic_deca

from liteusb import USBDevice
from liteusb.gateware.interface.ulpi import ULPIInterface

# CRG (Clock/Reset Generator) -----------------------------------------------

class DecaUSBCrg(LiteXModule):
    """DECA clock/reset generator — single-PLL architecture.

    A single MAX10 PLL from the PHY's clk60 (H11) generates the usb clock,
    which also drives the ULPI REFCLK (W3) and (with sys_from_usb=True)
    the sys/sync domains.  This mirrors the original Amaranth/LUNA clock
    generator at
      https://github.com/amaranth-farm/deca-usb2-audio-interface
      (gateware/arrow_deca.py, ArrowDECAClockAndResetController)

    A second clk50 PLL is created only when sys_from_usb=False (for the
    standalone sys clock).  When sys_from_usb=True, no clk50 PLL is
    instantiated — the raw 50MHz oscillator drives the POR counter directly.
    """
    def __init__(self, platform, sys_clk_freq, ulpi=None, clk60=None, sys_from_usb=True, with_por=True):
        self.rst     = Signal()
        self.cd_sys  = ClockDomain()
        self.cd_usb  = ClockDomain()

        clk50 = platform.request("clk50")

        # clk50-fed PLL: only needed when sys is NOT derived from usb.
        # When sys_from_usb=True, the raw clk50 pin is used directly for the
        # POR counter (below); no PLL is needed here at all.
        if not sys_from_usb:
            self.pll = pll = Max10PLL(speedgrade="-6")
            self.comb += pll.reset.eq(self.rst)
            pll.register_clkin(clk50, 50e6)
            pll.create_clkout(self.cd_sys, sys_clk_freq)

        # ── USB PLL (single-PLL, matches the original design) ────────────
        #   https://github.com/amaranth-farm/deca-usb2-audio-interface
        #   gateware/arrow_deca.py, ArrowDECAClockAndResetController
        #
        # Single PLL from PHY clk60 drives:
        #   1. cd_usb (internal usb/sync domains)
        #   2. ulpi.clk (W3, REFCLK to PHY)
        # The original uses an ALTPLL Instance directly (output 0 feeds both
        # ClockSignal("usb") and ClockSignal("sync")).  The PLL free-runs at
        # power-on, driving REFCLK to bootstrap the PHY.
        if clk60 is not None and ulpi is not None:
            platform.add_period_constraint(clk60, 1e9/60e6)

            self.usb_pll = pll = Max10PLL(speedgrade="-6")
            self.comb += pll.reset.eq(self.rst)
            pll.register_clkin(clk60, 60e6)
            # with_reset=False: PLL free-runs without locking, so REFCLK is
            # always present.  The original ALTPLL Instance also leaves reset
            # unconnected, letting the PLL free-run.
            pll.create_clkout(self.cd_usb, 60e6,
                phase=int(os.getenv("USB_PLL_PHASE", "-120")),
                with_reset=False)

            # Drive ULPI REFCLK (W3) from the usb clock — same PLL output
            # that drives the internal domains.  The original does the same:
            # ArrowDECAClockAndResetController feeds ulpi.clk from the PLL's
            # 60MHz output.
            self.comb += ulpi.clk.eq(ClockSignal("usb"))

            # Power-on PHY reset pulse (~42ms), clocked by the raw 50MHz
            # oscillator (always present, independent of PHY state).
            if with_por:
                self.cd_por = ClockDomain()
                self.comb += self.cd_por.clk.eq(clk50)
                self.por = por = Signal(21, reset=2**21 - 1, reset_less=True)
                self.comb += ResetSignal("usb").eq(por != 0)
                self.sync.por += If(por != 0, por.eq(por - 1))
            else:
                self.por = Signal(21, reset_less=True)

            # Optionally run sys on the usb clock (single clock net).
            if sys_from_usb:
                self.comb += ClockSignal("sys").eq(ClockSignal("usb"))
            else:
                # sys and usb are asynchronous (different PLLs).
                platform.add_false_path_constraints(self.cd_sys.clk, self.cd_usb.clk)

# SoC base -------------------------------------------------------------------

class DecaUSBSoC(SoCCore):
    """Base SoC for LiteUSB examples on the Terasic DECA (MAX10 + TUSB1210).

    Subclasses must override :meth:`add_usb_device`, create their USB device
    on the provided ``ulpi`` record (with ``handle_clocking=False``) and set
    ``self.usb`` to the :class:`USBDevice` instance. Optionally override
    :meth:`add_user_leds` to drive LEDs 4-7.
    """

    def __init__(self, sys_clk_freq=50e6,
        debug_leds        = False,
        sys_from_usb      = False,
        with_por          = True,
        **kwargs):

        self.platform = platform = terasic_deca.Platform()

        usb_ctrl  = platform.request("usb", 0)
        clk60     = platform.request("clk60", 0)
        ulpi_plat = platform.request("ulpi", 0)

        self.crg = DecaUSBCrg(platform, sys_clk_freq, ulpi=ulpi_plat, clk60=clk60,
            sys_from_usb=sys_from_usb, with_por=with_por)

        # ULPI I/O timing per TUSB1210 datasheet (sec. 6.14), PHY in clock
        # output mode (PHY generates the 60MHz, as wired on the DECA):
        #   FPGA -> PHY: setup 6.0ns, hold 0ns
        #   PHY -> FPGA: output delay 1.2ns .. 5.0ns
        # Constrain against the PHY's 60MHz clock (clk600 pin).
        #
        # Set LITEUSB_NO_ULPI_TIMING=1 to skip these constraints (LUNA-style
        # "just let the fitter handle it" approach — higher Fmax on MAX10
        # where the combinational ULPI path is too deep to meet the datasheet
        # numbers, but risks fit-dependent byte dup/drop).
        if not int(os.getenv("LITEUSB_NO_ULPI_TIMING", "0")):
            platform.toolchain.additional_sdc_commands += [
                "set_input_delay  -clock [get_clocks {clk600}] -max 5.0 [get_ports {ulpi0_dir ulpi0_nxt ulpi0_data[*]}]",
                "set_input_delay  -clock [get_clocks {clk600}] -min 1.2 [get_ports {ulpi0_dir ulpi0_nxt ulpi0_data[*]}]",
                "set_output_delay -clock [get_clocks {clk600}] -max 6.0 [get_ports {ulpi0_stp ulpi0_data[*]}]",
                "set_output_delay -clock [get_clocks {clk600}] -min 0.0 [get_ports {ulpi0_stp ulpi0_data[*]}]",
            ]
        # The POR counter runs on the raw clk50 pin; its CDC into the usb
        # domain is quasi-static (counts down once, then constant).
        platform.toolchain.additional_sdc_commands += [
            "set_false_path -from [get_clocks {clk50}] -to [get_clocks {usb_clk}]",
        ]

        if kwargs.get("uart_name", "stub") == "serial":
            kwargs["uart_name"] = "jtag_uart"

        SoCCore.__init__(self, platform, sys_clk_freq,
            ident="LiteUSB example SoC on Terasic DECA", **kwargs)

        # ── ULPI hookup ──────────────────────────────────────────────────
        self.comb += usb_ctrl.cs.eq(1)

        ulpi = ULPIInterface()
        self.comb += [
            ulpi.dir.eq(ulpi_plat.dir),
            ulpi.nxt.eq(ulpi_plat.nxt),
            ulpi_plat.stp.eq(ulpi.stp),
            ulpi_plat.reset_n.eq(~ulpi.rst),
        ]

        # ULPI data bus — TSTriple.get_tristate generates proper
        # altiobuf_bidir IO buffers (output = value, else high-Z)
        self.specials += ulpi.data.get_tristate(ulpi_plat.data)

        # ── Example-specific USB device ──────────────────────────────────
        self.usb = None
        self.add_usb_device(ulpi)
        assert self.usb is not None, "add_usb_device() must set self.usb"

        # ── LEDs ─────────────────────────────────────────────────────────
        if debug_leds:
            self._add_sticky_debug_leds(platform, ulpi)
        else:
            self._add_status_leds(platform)
            self.add_user_leds()

    # Hooks -----------------------------------------------------------------

    def add_usb_device(self, ulpi):
        """Create the example's USB device on ``ulpi``; must set ``self.usb``."""
        raise NotImplementedError

    def add_user_leds(self):
        """Optional: drive user_led 4-7 (called only when not debug_leds)."""
        pass

    # LED helpers -----------------------------------------------------------

    def _add_status_leds(self, platform):
        # USB Status LEDs (active-low, per DECA hardware — invert explicitly):
        #   LED0: TX activity   LED1: RX activity
        #   LED2: suspended     LED3: bus reset detected
        for i, sig in enumerate([
                self.usb.tx_activity_led, self.usb.rx_activity_led,
                self.usb.suspended, self.usb.reset_detected]):
            self.comb += platform.request("user_led", i).eq(~sig)

    def _add_sticky_debug_leds(self, platform, ulpi):
        # Diagnostic sticky LEDs: each latches an event/level so even
        # single-cycle pulses are visible. LED7 = sys heartbeat (alive /
        # polarity sanity).
        #   LED0: usb PLL locked
        #   LED1: usb heartbeat ~1Hz (60MHz domain running)
        #   LED2: sticky — DIR ever seen (PHY took the bus)
        #   LED3: sticky — PHY register write ever completed
        #   LED4: sticky — VBUS valid ever reported (RxCmd traffic OK)
        #   LED5: sticky — RX active ever (packet reception started)
        #   LED6: sticky — HS chirp entered (current_speed==HIGH ever)
        #   LED7: sys heartbeat ~1.5Hz
        usb = self.usb
        hb_usb = Signal(26)
        hb_sys = Signal(25)
        self.sync.usb += hb_usb.eq(hb_usb + 1)
        self.sync     += hb_sys.eq(hb_sys + 1)

        st_dir, st_wr, st_vbus, st_rxact, st_hs = Signal(5)
        self.sync.usb += [
            If(ulpi.dir,             st_dir.eq(1)),
            If(usb.translator.busy,  st_wr.eq(1)),
            If(usb.utmi.vbus_valid,  st_vbus.eq(1)),
            If(usb.utmi.rx_active,   st_rxact.eq(1)),
            If(usb.speed == 0b00,    st_hs.eq(1)),
        ]
        self.comb += [
            platform.request("user_led", 0).eq(self.crg.usb_pll.locked),
            platform.request("user_led", 1).eq(hb_usb[25]),
            platform.request("user_led", 2).eq(st_dir),
            platform.request("user_led", 3).eq(st_wr),
            platform.request("user_led", 4).eq(st_vbus),
            platform.request("user_led", 5).eq(st_rxact),
            platform.request("user_led", 6).eq(st_hs),
            platform.request("user_led", 7).eq(hb_sys[24]),
        ]

# CLI ------------------------------------------------------------------------

def deca_main(soc_cls, description):
    """Shared command line for DECA LiteUSB examples."""
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(
        platform    = terasic_deca.Platform,
        description = description)
    # CPU-less by default (overridable with --cpu-type; the ACM console
    # example overrides this with vexriscv itself).
    parser.set_defaults(cpu_type="None")
    # No UART by default: these examples don't use one (the ACM console
    # example provides its own); saves logic and JTAG-hub conflicts.
    parser.set_defaults(uart_name="stub")

    parser.add_target_argument("--sys-clk-freq", default=50e6, type=float,
        help="System clock frequency.")
    parser.add_target_argument("--debug-leds", action="store_true",
        help="Sticky diagnostic LEDs (PLL lock, DIR, reg writes, VBUS, RX, chirp).")
    parser.add_target_argument("--no-por", action="store_true",
        help="Disable the power-on PHY reset pulse (debug).")
    args = parser.parse_args()

    soc = soc_cls(
        sys_clk_freq = args.sys_clk_freq,
        debug_leds   = args.debug_leds,
        with_por     = not args.no_por,
        **parser.soc_argdict)

    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))
