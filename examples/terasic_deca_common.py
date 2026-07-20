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

- ``DecaUSBCrg``    — sys PLL (clk50) + usb PLL (clk60 from PHY, -120° phase)
- ``DecaUSBSoC``    — SoCCore base: ULPI hookup, status/diagnostic LEDs, ISSP
- ``deca_main``     — shared command line (build/load, --debug-leds, --with-issp)

Clock architecture:
  TUSB1210 PHY → clk60 (H11, input) → MAX10 PLL → cd_usb (60MHz, -120°)
                                                → ulpi.clk (W3, REFCLK back to PHY)

NOTE: the usb clock is created with ``with_reset=False``. The PHY only
produces its 60MHz CLOCK output once it sees REFCLK; gating the usb domain
reset on PLL lock would hold the PHY in reset and deadlock the clock loop.

Usage (in an example):

    from terasic_deca_common import DecaUSBSoC, deca_main

    class _DecaSoC(DecaUSBSoC):
        def add_usb_device(self, ulpi):
            self.submodules.dev = MyDevice(ulpi, handle_clocking=False)
            self.usb = self.dev.usb

    deca_main(_DecaSoC, "My Device on Terasic DECA")
"""

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
    """DECA clock/reset generator: 50MHz sys + 60MHz ULPI usb domains.

    With ``sys_from_usb=True`` the sys domain is driven directly from the usb
    clock (LUNA DECA style, ``ClockSignal("sys") = ClockSignal("usb")``): the
    whole design then runs on a single clock net, which avoids CDC issues
    between sys-clocked logic (CPU, UART, ACM FIFOs) and the usb domain.
    """
    def __init__(self, platform, sys_clk_freq, ulpi=None, clk60=None, sys_from_usb=False, with_por=True):
        self.rst     = Signal()
        self.cd_sys  = ClockDomain()
        self.cd_usb  = ClockDomain()

        clk50 = platform.request("clk50")

        # clk50-fed PLL: always created. Generates the sys clock (unless
        # sys_from_usb) AND the 60MHz PHY REFCLK (ulpi.clk), which must be
        # stable from power-on (see note at the usb PLL below).
        self.pll = pll = Max10PLL(speedgrade="-6")
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk50, 50e6)
        if not sys_from_usb:
            pll.create_clkout(self.cd_sys, sys_clk_freq)

        # USB ULPI PLL — 60MHz from PHY, -120° phase shift
        if clk60 is not None and ulpi is not None:
            # Constrain the 60MHz input from the PHY so the usb domain is
            # covered by timing analysis (LiteX does not add it automatically).
            platform.add_period_constraint(clk60, 1e9/60e6)

            self.usb_pll = pll = Max10PLL(speedgrade="-6")
            self.comb += pll.reset.eq(self.rst)
            pll.register_clkin(clk60, 60e6)
            # with_reset=False: do NOT gate the usb domain reset on PLL lock.
            # The PHY only produces its 60MHz CLOCK output once it sees REFCLK
            # (driven by our usb clock); if usb_rst is held while the PLL is
            # unlocked, the PHY is held in reset and the clock loop never
            # bootstraps (deadlock). LUNA leaves the PHY reset deasserted too.
            # usb domain clock: 60MHz from the PHY clkout, phase -120°
            # (same shift as the reference LUNA DECA design).
            pll.create_clkout(self.cd_usb, 60e6, phase=-120, with_reset=False)

            # Power-on PHY reset pulse (~42ms, clocked by the raw 50MHz
            # oscillator which is always present — NOT by the usb clock,
            # which free-runs at an arbitrary rate while the PHY's CLOCK
            # output is off and would stretch the pulse to minutes).
            # The PHY (TUSB1210) can be stuck e.g. in suspend across FPGA
            # reconfigures (it keeps its register state and stops CLOCK in
            # suspend, so the usb PLL would never lock). Pulsing the usb
            # domain reset at startup resets the PHY via UTMITranslator
            # (ulpi.rst = ResetSignal("usb")) and always re-bootstraps the
            # clock loop: PHY reset -> REFCLK seen -> CLOCK out -> PLL lock.
            if with_por:
                self.cd_por = ClockDomain()
                self.comb += self.cd_por.clk.eq(clk50)
                self.por = por = Signal(21, reset=2**21 - 1, reset_less=True)
                self.comb += ResetSignal("usb").eq(por != 0)
                self.sync.por += If(por != 0, por.eq(por - 1))
            else:
                self.por = Signal(21, reset_less=True)

            # Drive the ULPI REFCLK (W3) from the clk50-fed PLL (second
            # output), NOT from the usb PLL output. The usb PLL is fed by the
            # PHY's CLOCK output, so sourcing REFCLK from it made the clock
            # loop circular: PHY CLOCK only exists once the PHY locks to
            # REFCLK, which was the free-running usb PLL at an arbitrary
            # frequency. Convergence was luck (flaky HS/FS/no-enumerate).
            # A REFCLK that is stable from power-on makes the bootstrap
            # deterministic: REFCLK ok -> PHY CLOCK out -> usb PLL lock.
            self.cd_ref = ClockDomain()
            self.pll.create_clkout(self.cd_ref, 60e6)
            self.comb += ulpi.clk.eq(ClockSignal("ref"))

            # REFCLK-alive indicator (visible on the ISSP probe).
            self.ref_toggle = Signal()
            self.sync.ref += self.ref_toggle.eq(~self.ref_toggle)

            # sys and usb are asynchronous to each other (different PLLs);
            # legitimate CDC paths (async FIFOs, LED sticky flags) must not
            # be timing-analyzed.
            platform.add_false_path_constraints(self.cd_sys.clk, self.cd_usb.clk)

            # Optionally run sys on the usb clock (single clock net).
            if sys_from_usb:
                self.comb += ClockSignal("sys").eq(ClockSignal("usb"))

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
        with_issp         = False,
        sys_from_usb      = False,
        with_por          = True,
        **kwargs):

        self.platform = platform = terasic_deca.Platform()

        usb_ctrl  = platform.request("usb", 0)
        clk60     = platform.request("clk60", 0)
        ulpi_plat = platform.request("ulpi", 0)

        self.crg = DecaUSBCrg(platform, sys_clk_freq, ulpi=ulpi_plat, clk60=clk60,
            sys_from_usb=sys_from_usb, with_por=with_por)

        # NOTE: no I/O delay constraints on the ULPI pins. The 60MHz ULPI
        # timing is loose by design (reference LUNA DECA design constrains
        # nothing either); adding constraints the fitter cannot meet just
        # degrades placement.

        if with_issp:
            # jtag_uart and ISSP both claim the MAX10 JTAG primitive
            # (Quartus Error 12143); ISSP wins, UART becomes a stub.
            kwargs["uart_name"] = "stub"
        elif kwargs.get("uart_name", "stub") == "serial":
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

        # ── In-System Sources & Probes (JTAG status readout) ─────────────
        if with_issp:
            self._add_issp(ulpi)

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

    # ISSP ------------------------------------------------------------------

    def _add_issp(self, ulpi):
        usb = self.usb
        probe = Cat(
            self.crg.usb_pll.locked,        # 0
            ulpi.dir,                       # 1
            ulpi.nxt,                       # 2
            ulpi.stp,                       # 3
            usb.speed,                      # 5:4
            usb.utmi.line_state,            # 7:6
            usb.utmi.last_rx_command,       # 15:8
            usb.utmi.vbus_valid,            # 16
            usb.utmi.session_valid,         # 17
            usb.utmi.session_end,           # 18
            usb.utmi.rx_active,             # 19
            usb.utmi.rx_valid,              # 20
            usb.utmi.tx_ready,              # 21
            usb.utmi.busy,                  # 22
            usb.suspended,                  # 23
            usb.connect,                    # 24
            usb.reset_detected,             # 25
            usb.frame_number,               # 36:26
            usb.utmi.rx_data,               # 44:37
            self.crg.por,                   # 65:45 (0 once POR pulse done)
            usb.utmi.op_mode,               # 67:66 (2=CHIRP)
            usb.utmi.xcvr_select,           # 69:68 (0=HS,1=FS)
            usb.utmi.term_select,           # 70
            usb.utmi.suspend,               # 71
            self.crg.ref_toggle,            # 72 (toggles iff REFCLK runs)
        )
        self.specials += Instance("altsource_probe",
            p_probe_width       = len(probe),
            p_source_width      = 1,
            p_instance_id       = "USB0",
            p_sld_auto_instance_index = "YES",
            i_probe             = probe,
            o_source            = Signal(1),
            i_source_clk        = 0,
            i_source_ena        = 0,
        )

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
    parser.add_target_argument("--with-issp", action="store_true",
        help="Add In-System Sources & Probes (JTAG status readout of USB state).")
    parser.add_target_argument("--no-por", action="store_true",
        help="Disable the power-on PHY reset pulse (debug).")
    args = parser.parse_args()

    soc = soc_cls(
        sys_clk_freq = args.sys_clk_freq,
        debug_leds   = args.debug_leds,
        with_issp    = args.with_issp,
        with_por     = not args.no_por,
        **parser.soc_argdict)

    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))
