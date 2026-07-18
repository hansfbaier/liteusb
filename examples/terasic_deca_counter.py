#!/usr/bin/env python3
#
# Terasic DECA LiteUSB Counter Device target
# USB 2.0 Bulk-IN endpoint that streams a monotonic counter
#
# Clock architecture:
#   TUSB1210 PHY → clk60 (H11, input) → MAX10 PLL → cd_usb (60MHz, -120°)
#                                                 → ulpi.clk (W3, REFCLK back to PHY)
#
# NOTE: the usb clock is created with with_reset=False. The PHY only produces
# its 60MHz CLOCK output once it sees REFCLK; gating the usb domain reset on
# PLL lock would hold the PHY in reset and deadlock the clock loop.
#
# Build & load:
#   python3 terasic_deca_counter.py --with-usb-device --cpu-type=None --build
#   (then load terasic_deca.sof with quartus_pgm or --load)
#
# Useful options:
#   --debug-leds   sticky diagnostic LEDs (PLL lock, DIR, reg writes, VBUS,
#                  RX activity, HS chirp)
#   --with-issp    In-System Sources & Probes (JTAG readout of USB state;
#                  forces uart_name="stub" since jtag_uart conflicts with ISSP)
#

from migen import *
from litex_boards.platforms import terasic_deca

from litex.gen import *
from litex.soc.cores.clock import Max10PLL
from litex.soc.integration.soc import *
from litex.soc.integration.builder import *

from liteusb import USBDevice
from liteusb.gateware.interface.ulpi import ULPIInterface
from liteusb.gateware.usb.usb2.endpoints.stream import USBStreamInEndpoint
from usb_protocol.emitters import DeviceDescriptorCollection

# CRG (Clock/Reset Generator) -----------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, ulpi=None, clk60=None):
        self.rst     = Signal()
        self.cd_sys  = ClockDomain()
        self.cd_usb  = ClockDomain()

        clk50 = platform.request("clk50")

        # System PLL
        self.pll = pll = Max10PLL(speedgrade="-6")
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk50, 50e6)
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
            pll.create_clkout(self.cd_usb, 60e6, phase=-120, with_reset=False)

            # Drive the ULPI clock BACK to the PHY on W3
            self.comb += ulpi.clk.eq(ClockSignal("usb"))

# SoC -----------------------------------------------------------------------

class CounterSoC(SoCCore):
    """DECA SoC with LiteUSB counter device (bulk-IN streaming counter)."""

    BULK_ENDPOINT_NUMBER = 1
    MAX_BULK_PACKET_SIZE = 512

    def __init__(self, sys_clk_freq=50e6,
        with_usb_device   = False,
        with_led_chaser   = True,
        debug_leds        = False,
        with_issp         = False,
        **kwargs):

        self.platform = platform = terasic_deca.Platform()

        if with_usb_device:
            usb_ctrl   = platform.request("usb", 0)
            clk60      = platform.request("clk60", 0)
            ulpi_plat  = platform.request("ulpi", 0)
        else:
            usb_ctrl = clk60 = ulpi_plat = None

        self.crg = _CRG(platform, sys_clk_freq, ulpi=ulpi_plat, clk60=clk60)

        real_uart_name = kwargs.get("uart_name", "serial")
        if with_issp:
            # jtag_uart occupies the JTAG debug hub; ISSP needs it too.
            kwargs["uart_name"] = "stub"
        elif real_uart_name == "serial":
            kwargs["uart_name"] = "jtag_uart"

        SoCCore.__init__(self, platform, sys_clk_freq,
            ident="LiteX Counter SoC on Terasic DECA", **kwargs)

        # ── LiteUSB Counter Device ─────────────────────────────────────────
        if with_usb_device:
            self.comb += usb_ctrl.cs.eq(1)

            # ULPI adapter
            ulpi = ULPIInterface()
            self.comb += [
                ulpi.dir.eq(ulpi_plat.dir),
                ulpi.nxt.eq(ulpi_plat.nxt),
                ulpi_plat.stp.eq(ulpi.stp),
                ulpi_plat.reset_n.eq(~ulpi.rst),
            ]

            # ULPI data bus — TSTriple.get_tristate generates proper
            # altiobuf_bidir IO buffers (output = value, else high‑Z)
            self.specials += ulpi.data.get_tristate(ulpi_plat.data)

            # Create USB device
            self.submodules.usb = usb = USBDevice(bus=ulpi, handle_clocking=False)

            # Standard USB descriptors
            descriptors = DeviceDescriptorCollection()
            with descriptors.DeviceDescriptor() as d:
                d.idVendor      = 0x1209
                d.idProduct     = 0x0001
                d.iManufacturer = "LiteUSB"
                d.iProduct      = "DECA Counter Device"
                d.iSerialNumber = "0001"
                d.bNumConfigurations = 1

            with descriptors.ConfigurationDescriptor() as c:
                with c.InterfaceDescriptor() as i:
                    i.bInterfaceNumber = 0
                    with i.EndpointDescriptor() as e:
                        e.bEndpointAddress = 0x80 | self.BULK_ENDPOINT_NUMBER
                        e.wMaxPacketSize   = self.MAX_BULK_PACKET_SIZE

            usb.add_standard_control_endpoint(descriptors)

            # ── Counter endpoint (bulk-IN streaming counter) ───────────────
            stream_ep = USBStreamInEndpoint(
                endpoint_number=self.BULK_ENDPOINT_NUMBER,
                max_packet_size=self.MAX_BULK_PACKET_SIZE
            )
            usb.add_endpoint(stream_ep)

            counter = Signal(8)
            self.sync.usb += If(stream_ep.stream.ready, counter.eq(counter + 1))

            self.comb += [
                stream_ep.stream.valid   .eq(1),
                stream_ep.stream.payload .eq(counter),
                usb.connect               .eq(1),
            ]

            if debug_leds:
                # Diagnostic sticky LEDs: each latches an event/level so even
                # single-cycle pulses are visible. LED7 = sys heartbeat (alive
                # / polarity sanity).
                #   LED0: usb PLL locked
                #   LED1: usb heartbeat ~1Hz (60MHz domain running)
                #   LED2: sticky — DIR ever seen (PHY took the bus)
                #   LED3: sticky — PHY register write ever completed
                #   LED4: sticky — VBUS valid ever reported (RxCmd traffic OK)
                #   LED5: sticky — RX active ever (packet reception started)
                #   LED6: sticky — HS chirp entered (current_speed==HIGH ever)
                #   LED7: sys heartbeat ~1.5Hz
                hb_usb = Signal(26)
                hb_sys = Signal(25)
                self.sync.usb += hb_usb.eq(hb_usb + 1)
                self.sync     += hb_sys.eq(hb_sys + 1)

                st_dir    = Signal()
                st_wr     = Signal()
                st_vbus   = Signal()
                st_rxact  = Signal()
                st_hs     = Signal()
                self.sync.usb += [
                    If(ulpi.dir,                     st_dir.eq(1)),
                    If(usb.translator.busy,          st_wr.eq(1)),
                    If(usb.utmi.vbus_valid,          st_vbus.eq(1)),
                    If(usb.utmi.rx_active,           st_rxact.eq(1)),
                    If(usb.speed == 0b00,            st_hs.eq(1)),
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
            else:
                # USB Status LEDs (active-low, per DECA hardware; matching
                # deca-usb2-audio-interface pattern where Amaranth's invert=True
                # handles the physical inversion — we must invert explicitly):
                #   LED0: TX activity
                #   LED1: RX activity
                #   LED2: suspended
                #   LED3: bus reset detected
                #   LED4-7: lower nibble of streaming counter
                try:
                    self.comb += [
                        platform.request("user_led", 0).eq(~usb.tx_activity_led),
                        platform.request("user_led", 1).eq(~usb.rx_activity_led),
                        platform.request("user_led", 2).eq(~usb.suspended),
                        platform.request("user_led", 3).eq(~usb.reset_detected),
                        platform.request("user_led", 4).eq(~counter[0]),
                        platform.request("user_led", 5).eq(~counter[1]),
                        platform.request("user_led", 6).eq(~counter[2]),
                        platform.request("user_led", 7).eq(~counter[3]),
                    ]
                except:
                    pass

        # ── In-System Sources & Probes (JTAG status readout) ─────────────
        if with_issp and with_usb_device:
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

        if with_led_chaser and not with_usb_device:
            from litex.soc.cores.led import LedChaser
            self.leds = LedChaser(
                pads         = platform.request_all("user_led"),
                sys_clk_freq = sys_clk_freq)

# Build ---------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(
        platform    = terasic_deca.Platform,
        description = "LiteX Counter SoC on Terasic DECA with LiteUSB ULPI Device")

    parser.add_target_argument("--sys-clk-freq", default=50e6, type=float,
        help="System clock frequency.")
    parser.add_target_argument("--with-usb-device", action="store_true",
        help="Enable LiteUSB ULPI counter device.")
    parser.add_target_argument("--with-led-chaser", action="store_true",
        help="Enable LED chaser.")
    parser.add_target_argument("--debug-leds", action="store_true",
        help="Map diagnostic signals (heartbeats, ULPI pins, line state) to LEDs.")
    parser.add_target_argument("--with-issp", action="store_true",
        help="Add In-System Sources & Probes (JTAG status readout of USB state).")
    args = parser.parse_args()

    soc = CounterSoC(
        sys_clk_freq    = args.sys_clk_freq,
        with_usb_device  = args.with_usb_device,
        with_led_chaser  = args.with_led_chaser,
        debug_leds       = args.debug_leds,
        with_issp        = args.with_issp,
        **parser.soc_argdict)

    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()
