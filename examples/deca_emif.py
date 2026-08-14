#!/usr/bin/env python3
#
# DECA DDR3 EMIF (Altera soft DDR3 controller) -> Wishbone integration.
#
# Wraps the Intel EMIF IP generated for the Terasic DECA (MAX10 10M50,
# MT41K256M16HA-125 DDR3L, 16-bit, 512 MB, 300 MHz mem clock / 600 MT/s).
# IP sources vendored from the Terasic DECA CD (DECA_DDR3_Nios_Test,
# qsys-generated with Quartus 21.1) live in vendor/deca_emif/.
#
# The IP exposes a 64-bit Avalon-MM slave (avl) synchronous to its own
# generated afi_clk output (150 MHz). This module bridges a 32-bit
# Wishbone slave (SoC bus clock domain) to that Avalon interface:
#   - 32-bit reads  -> 64-bit Avalon read + half-word select + one-line
#                     pair cache (adjacent word hits the cache)
#   - 32-bit writes -> 64-bit Avalon write with byte enables (no RMW)
#   - single outstanding transaction, async handshake CDC between the
#     two clock domains
#   - the bridge holds Wishbone acks until EMIF local_init_done (so the
#     CPU/BIOS cannot read garbage during DDR3 calibration)
#
# Usage:
#     self.submodules.emif = emif = DecaEMIF(platform)
#     self.bus.add_slave("main_ram", emif.bus,
#         region=SoCRegion(origin=0x40000000, size=512*1024*1024,
#                          mode="rwx", cached=True))
#     self.add_constant("MAIN_RAM_SIZE", 512*1024*1024)

import os

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStatus

# EMIF IP vendor directory (relative to this file)
VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "deca_emif")

# Wishbone slave: 32-bit, word-addressed, 27 address bits (512 MB)
WB_DATA_WIDTH = 32
WB_ADDR_WIDTH = 27
AVL_ADDR_WIDTH = 26  # 64-bit words, 512 MB


class DecaEMIF(LiteXModule):
    """DECA DDR3 EMIF IP + 32-bit Wishbone slave bridge."""

    def __init__(self, platform, cd_sys):
        self.bus = bus = wishbone.Interface(
            data_width = WB_DATA_WIDTH,
            adr_width  = WB_ADDR_WIDTH,
            addressing = "word")
        bus.clock_domain = "sys"

        ddram    = platform.request("ddram")
        ddr3_clk = platform.request("ddr3_clk")

        # ── afi clock domain (from the EMIF's own PLL, 150 MHz) ─────────
        self.clock_domains.cd_afi = ClockDomain()
        self.afi_clk = Signal()

        afi_reset_n = Signal()
        self.comb += [
            self.cd_afi.clk.eq(self.afi_clk),
            self.cd_afi.rst.eq(~afi_reset_n),
        ]

        # Status Signals / CSRs
        self.init_done   = Signal()
        self.cal_success = Signal()
        self.cal_fail    = Signal()
        self.pll_locked  = Signal()
        self.submodules.emif_status = CSRStatus(4, name="emif_status",
            description="EMIF status: [0]=pll_locked [1]=cal_success [2]=cal_fail [3]=init_done")
        self.comb += self.emif_status.status.eq(Cat(
            self.pll_locked, self.cal_success, self.cal_fail, self.init_done))

        # ── EMIF instance ───────────────────────────────────────────────
        avl_ready       = Signal()
        avl_burstbegin  = Signal()
        avl_addr        = Signal(AVL_ADDR_WIDTH)
        avl_rdata_valid = Signal()
        avl_rdata       = Signal(64)
        avl_wdata       = Signal(64)
        avl_be          = Signal(8)
        avl_read_req    = Signal()
        avl_write_req   = Signal()
        avl_size        = Signal(3)

        self.specials += Instance("deca_qsys_mem_if_ddr3_emif",
            i_pll_ref_clk    = ddr3_clk,
            i_global_reset_n = ~ResetSignal("sys"),
            i_soft_reset_n   = Constant(1),

            o_afi_clk             = self.afi_clk,
            o_afi_half_clk        = Open(),
            o_afi_reset_n         = afi_reset_n,
            o_afi_reset_export_n  = Open(),

            o_mem_a         = ddram.a,
            o_mem_ba        = ddram.ba,
            io_mem_ck       = ddram.clk_p,
            io_mem_ck_n     = ddram.clk_n,
            o_mem_cke       = ddram.cke,
            o_mem_cs_n      = ddram.cs_n,
            o_mem_dm        = ddram.dm,
            o_mem_ras_n     = ddram.ras_n,
            o_mem_cas_n     = ddram.cas_n,
            o_mem_we_n      = ddram.we_n,
            o_mem_reset_n   = ddram.reset_n,
            io_mem_dq       = ddram.dq,
            io_mem_dqs      = ddram.dqs_p,
            io_mem_dqs_n    = ddram.dqs_n,
            o_mem_odt       = ddram.odt,

            o_avl_ready       = avl_ready,
            i_avl_burstbegin  = avl_burstbegin,
            i_avl_addr        = avl_addr,
            o_avl_rdata_valid = avl_rdata_valid,
            o_avl_rdata       = avl_rdata,
            i_avl_wdata       = avl_wdata,
            i_avl_be          = avl_be,
            i_avl_read_req    = avl_read_req,
            i_avl_write_req   = avl_write_req,
            i_avl_size        = avl_size,

            o_local_init_done    = self.init_done,
            o_local_cal_success  = self.cal_success,
            o_local_cal_fail     = self.cal_fail,
            o_pll_locked         = self.pll_locked,
            o_pll_mem_clk        = Open(),
            o_pll_write_clk      = Open(),
            o_pll_capture0_clk   = Open(),
            o_pll_capture1_clk   = Open(),
        )

        # ── Wishbone <-> Avalon bridge ──────────────────────────────────
        self.submodules.bridge = bridge = EMIFWishboneBridge(
            bus, self.cd_afi, self.init_done)
        self.comb += [
            avl_burstbegin.eq(bridge.avl_cmd_issue),
            avl_addr.eq(bridge.avl_addr),
            avl_wdata.eq(bridge.avl_wdata),
            avl_be.eq(bridge.avl_be),
            avl_read_req.eq(bridge.avl_read_req),
            avl_write_req.eq(bridge.avl_write_req),
            avl_size.eq(1),
            bridge.avl_ready.eq(avl_ready),
            bridge.avl_rdata_valid.eq(avl_rdata_valid),
            bridge.avl_rdata.eq(avl_rdata),
        ]

        # CDC constraints: sys <-> afi unrelated clocks
        platform.add_false_path_constraints(
            cd_sys.clk, self.cd_afi.clk)

        # IP SDC files (absolute paths so the IP-internal $script_dir
        # sources resolve to the vendor dir)
        for sdc in [
            "deca_qsys_mem_if_ddr3_emif_p0.sdc",
            "altera_avalon_dc_fifo.sdc",
            "altera_avalon_st_handshake_clock_crosser.sdc",
            "altera_reset_controller.sdc",
        ]:
            platform.toolchain.additional_sdc_commands.append(
                f"source {os.path.join(VENDOR_DIR, sdc)}")

        # IP source files
        for f in sorted(os.listdir(VENDOR_DIR)):
            if f.endswith((".v", ".sv")):
                platform.add_source(os.path.join(VENDOR_DIR, f))


class EMIFWishboneBridge(LiteXModule):
    """32-bit Wishbone slave (sys) <-> 64-bit Avalon master (afi).

    Single outstanding transaction. One 64-bit pair cache for reads.
    Writes use Avalon byte enables so no read-modify-write is needed.
    """

    def __init__(self, bus, cd_afi, init_done):
        # sys-domain signals
        cmd_valid_sys = Signal()
        cmd_we_sys    = Signal()
        cmd_addr_sys  = Signal(AVL_ADDR_WIDTH)  # 64-bit-word address
        cmd_half_sys  = Signal()                # which 32-bit half (wb.adr[0])
        cmd_sel_sys   = Signal(4)
        cmd_data_sys  = Signal(32)

        resp_valid_sys = Signal()
        resp_data_sys  = Signal(32)

        # afi-domain signals
        cmd_valid_afi = Signal()
        cmd_we_afi    = Signal()
        cmd_addr_afi  = Signal(AVL_ADDR_WIDTH)
        cmd_half_afi  = Signal()
        cmd_sel_afi   = Signal(4)
        cmd_data_afi  = Signal(32)

        resp_valid_afi = Signal()
        resp_data_afi  = Signal(32)

        # ── CDC sys -> afi (command) ────────────────────────────────────
        self.specials += [
            MultiReg(cmd_valid_sys, cmd_valid_afi, "afi"),
            MultiReg(cmd_we_sys,    cmd_we_afi,    "afi"),
            MultiReg(cmd_addr_sys,  cmd_addr_afi,  "afi"),
            MultiReg(cmd_half_sys,  cmd_half_afi,  "afi"),
            MultiReg(cmd_sel_sys,   cmd_sel_afi,   "afi"),
            MultiReg(cmd_data_sys,  cmd_data_afi,  "afi"),
            # ── CDC afi -> sys (response) ───────────────────────────────
            MultiReg(resp_valid_afi, resp_valid_sys, "sys"),
            MultiReg(resp_data_afi,  resp_data_sys,  "sys"),
        ]

        # ── sys domain: wishbone slave FSM ──────────────────────────────
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(bus.cyc & bus.stb,
                NextValue(cmd_we_sys,   bus.we),
                NextValue(cmd_addr_sys, bus.adr[1:27]),
                NextValue(cmd_half_sys, bus.adr[0]),
                NextValue(cmd_sel_sys,  bus.sel),
                NextValue(cmd_data_sys, bus.dat_w),
                NextValue(cmd_valid_sys, 1),
                NextState("WAIT"),
            )
        )
        fsm.act("WAIT",
            If(resp_valid_sys,
                bus.ack.eq(1),
                bus.dat_r.eq(resp_data_sys),
                NextValue(cmd_valid_sys, 0),
                NextState("DONE"),
            )
        )
        fsm.act("DONE",
            If(~bus.cyc,
                NextState("IDLE"),
            )
        )

        # ── afi domain: Avalon master FSM + pair cache ──────────────────
        # init_done resync into the afi domain (the EMIF asserts it on its
        # own clock; MultiReg aligns it with afi).
        init_done_sync = Signal()
        self.specials += MultiReg(init_done, init_done_sync, "afi")

        cache_valid = Signal()
        cache_addr  = Signal(AVL_ADDR_WIDTH)
        cache_data  = Signal(64)

        self.avl_cmd_issue = avl_cmd_issue = Signal()
        self.avl_read_req  = avl_read_req  = Signal()
        self.avl_write_req = avl_write_req = Signal()
        self.avl_addr      = avl_addr      = Signal(AVL_ADDR_WIDTH)
        self.avl_wdata     = avl_wdata     = Signal(64)
        self.avl_be        = avl_be        = Signal(8)
        self.avl_ready     = avl_ready     = Signal()
        self.avl_rdata_valid = avl_rdata_valid = Signal()
        self.avl_rdata     = avl_rdata     = Signal(64)

        fsm_afi = FSM(reset_state="IDLE")
        self.submodules.fsm_afi = ClockDomainsRenamer("afi")(fsm_afi)

        self.comb += avl_addr.eq(cmd_addr_afi)

        fsm_afi.act("IDLE",
            If(cmd_valid_afi & init_done_sync,  # only talk to a calibrated controller
                If(cmd_we_afi,
                    # 64-bit write, byte enables pick the half (no RMW)
                    avl_wdata.eq(0),
                    avl_be.eq(0),
                    If(cmd_half_afi,
                        avl_wdata[32:64].eq(cmd_data_afi),
                        avl_be[4:8].eq(cmd_sel_afi),
                    ).Else(
                        avl_wdata[0:32].eq(cmd_data_afi),
                        avl_be[0:4].eq(cmd_sel_afi),
                    ),
                    avl_write_req.eq(1),
                    avl_cmd_issue.eq(1),
                    If(avl_ready,
                        resp_valid_afi.eq(1),  # burst=1: done on accept
                        NextState("WRITE_DONE"),
                    ),
                ).Elif(cache_valid & (cache_addr == cmd_addr_afi),
                    # pair-cache hit: answer from the cached 64-bit word
                    If(cmd_half_afi,
                        resp_data_afi.eq(cache_data[32:64]),
                    ).Else(
                        resp_data_afi.eq(cache_data[0:32]),
                    ),
                    resp_valid_afi.eq(1),
                    NextState("READ_DONE"),
                ).Else(
                    avl_read_req.eq(1),
                    avl_cmd_issue.eq(1),
                    If(avl_ready,
                        NextState("READ_WAIT"),
                    ),
                ),
            ),
        )
        fsm_afi.act("READ_WAIT",
            If(avl_rdata_valid,
                NextValue(cache_valid, 1),
                NextValue(cache_addr,  cmd_addr_afi),
                NextValue(cache_data,  avl_rdata),
                If(cmd_half_afi,
                    resp_data_afi.eq(avl_rdata[32:64]),
                ).Else(
                    resp_data_afi.eq(avl_rdata[0:32]),
                ),
                resp_valid_afi.eq(1),
                NextState("READ_DONE"),
            ),
        )
        fsm_afi.act("READ_DONE",
            If(~cmd_valid_afi,
                NextState("IDLE"),
            ),
        )
        fsm_afi.act("WRITE_DONE",
            If(~cmd_valid_afi,
                NextState("IDLE"),
            ),
        )


# vim: set ts=4 sw=4 et:
