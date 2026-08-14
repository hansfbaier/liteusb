#!/usr/bin/env bash
#
# regenerate_deca_emif.sh — regenerate examples/vendor/deca_emif/ from the
# Terasic DECA CD (DECA_DDR3_Nios_Test demo) + Quartus qsys-generate.
#
# The Intel EMIF IP sources in vendor/deca_emif/ are NOT tracked in git:
# the Quartus Prime and Intel FPGA IP License Agreement does not permit
# redistributing generated IP HDL. They are regenerated locally with this
# script.
#
# Requirements:
#   - Terasic DECA CD, demo DECA_DDR3_Nios_Test (deca_qsys.qsys +
#     deca_qsys.sopcinfo). Get it from terasic.com (DECA CD / System CD).
#   - Quartus Prime Standard (21.1 tested; Lite Edition is sufficient —
#     the MAX10 DDR3 EMIF IP is included). qsys-generate must be available.
#
# Usage:
#   ./regenerate_deca_emif.sh                                  # auto-detect
#   ./regenerate_deca_emif.sh /path/to/DECA_DDR3_Nios_Test
#   DECA_CD=/path/to/DECA_DDR3_Nios_Test QUARTUS_ROOTDIR=/opt/intelFPGA_lite/21.1/quartus ./regenerate_deca_emif.sh
#   ./regenerate_deca_emif.sh --smoke-test                    # + standalone synthesis check
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/vendor/deca_emif"

# ── Arguments ──────────────────────────────────────────────────────────────
SRC=""
SMOKE_TEST=0
for arg in "$@"; do
    case "$arg" in
        --smoke-test) SMOKE_TEST=1 ;;
        -h|--help)    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            SRC="$arg" ;;
    esac
done

# ── Locate the Terasic DECA CD demo ────────────────────────────────────────
[ -n "$SRC" ] || SRC="${DECA_CD:-}"
if [ -z "$SRC" ]; then
    for cand in \
        /devel/HDL/DECA/Demonstrations/DECA_DDR3_Nios_Test \
        /media/*/DECA/Demonstrations/DECA_DDR3_Nios_Test \
        /run/media/*/DECA/Demonstrations/DECA_DDR3_Nios_Test \
        "$HOME"/DECA/Demonstrations/DECA_DDR3_Nios_Test; do
        if [ -f "$cand/deca_qsys.qsys" ]; then SRC="$cand"; break; fi
    done
fi
if [ -z "$SRC" ] || [ ! -f "$SRC/deca_qsys.qsys" ] || [ ! -f "$SRC/deca_qsys.sopcinfo" ]; then
    echo "ERROR: DECA_DDR3_Nios_Test not found."
    echo "Pass the directory as an argument or set DECA_CD= (needs deca_qsys.qsys + deca_qsys.sopcinfo)."
    exit 1
fi
echo "DECA CD demo: $SRC"

# ── Locate Quartus ─────────────────────────────────────────────────────────
QUARTUS="${QUARTUS_ROOTDIR:-}"
if [ -z "$QUARTUS" ]; then
    QUARTUS="/opt/intelFPGA_lite/21.1/quartus"
fi
if [ ! -x "$QUARTUS/sopc_builder/bin/qsys-generate" ]; then
    # Fall back to PATH.
    if command -v qsys-generate >/dev/null 2>&1; then
        QUARTUS="$(dirname "$(dirname "$(command -v qsys-generate)")")"
    else
        echo "ERROR: qsys-generate not found. Set QUARTUS_ROOTDIR=<quartus-install-dir>."
        exit 1
    fi
fi
export PATH="$QUARTUS/sopc_builder/bin:$QUARTUS/bin:$PATH"
echo "Quartus: $QUARTUS ($(qsys-generate --version 2>/dev/null | head -1 || echo 'version unknown'))"

# ── Generate with qsys-generate ────────────────────────────────────────────
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "$SRC/deca_qsys.qsys" "$SRC/deca_qsys.sopcinfo" "$WORK/"
echo "qsys-generate deca_qsys.qsys --synthesis=VERILOG"
( cd "$WORK" && qsys-generate deca_qsys.qsys --synthesis=VERILOG )
SUB="$WORK/deca_qsys/synthesis/submodules"
[ -f "$SUB/deca_qsys_mem_if_ddr3_emif.v" ] || { echo "ERROR: qsys-generate produced no EMIF wrapper."; exit 1; }

# ── Copy the EMIF subset into vendor/deca_emif ─────────────────────────────
# Exact file set needed by deca_emif.py (synthesis-verified with Quartus
# 21.1 + MAX10 10M50DAF484C6GES; nothing else from the demo is required).
FILES="
afi_mux_ddr3_ddrx.v
altera_avalon_dc_fifo.sdc
altera_avalon_dc_fifo.v
altera_avalon_mm_clock_crossing_bridge.v
altera_avalon_sc_fifo.v
altera_avalon_st_clock_crosser.v
altera_avalon_st_handshake_clock_crosser.sdc
altera_avalon_st_handshake_clock_crosser.v
altera_avalon_st_pipeline_base.v
altera_dcfifo_synchronizer_bundle.v
altera_gpio_lite.sv
altera_irq_clock_crosser.sv
altera_mem_if_sequencer_rst.sv
altera_merlin_address_alignment.sv
altera_merlin_arbitrator.sv
altera_merlin_burst_uncompressor.sv
altera_merlin_master_agent.sv
altera_merlin_master_translator.sv
altera_merlin_reorder_memory.sv
altera_merlin_slave_agent.sv
altera_merlin_slave_translator.sv
altera_merlin_traffic_limiter.sv
altera_merlin_width_adapter.sv
altera_reset_controller.sdc
altera_reset_controller.v
altera_reset_synchronizer.v
altera_std_synchronizer_nocut.v
alt_mem_ddrx_addr_cmd.v
alt_mem_ddrx_addr_cmd_wrap.v
alt_mem_ddrx_arbiter.v
alt_mem_ddrx_axi_st_converter.v
alt_mem_ddrx_buffer_manager.v
alt_mem_ddrx_buffer.v
alt_mem_ddrx_burst_gen.v
alt_mem_ddrx_burst_tracking.v
alt_mem_ddrx_cmd_gen.v
alt_mem_ddrx_controller_st_top.v
alt_mem_ddrx_controller.v
alt_mem_ddrx_csr.v
alt_mem_ddrx_dataid_manager.v
alt_mem_ddrx_ddr2_odt_gen.v
alt_mem_ddrx_ddr3_odt_gen.v
alt_mem_ddrx_define.iv
alt_mem_ddrx_ecc_decoder_32_syn.v
alt_mem_ddrx_ecc_decoder_64_syn.v
alt_mem_ddrx_ecc_decoder.v
alt_mem_ddrx_ecc_encoder_32_syn.v
alt_mem_ddrx_ecc_encoder_64_syn.v
alt_mem_ddrx_ecc_encoder_decoder_wrapper.v
alt_mem_ddrx_ecc_encoder.v
alt_mem_ddrx_fifo.v
alt_mem_ddrx_input_if.v
alt_mem_ddrx_list.v
alt_mem_ddrx_lpddr2_addr_cmd.v
alt_mem_ddrx_mm_st_converter.v
alt_mem_ddrx_odt_gen.v
alt_mem_ddrx_rank_timer.v
alt_mem_ddrx_rdata_path.v
alt_mem_ddrx_rdwr_data_tmg.v
alt_mem_ddrx_sideband.v
alt_mem_ddrx_tbp.v
alt_mem_ddrx_timing_param.v
alt_mem_ddrx_wdata_path.v
alt_mem_if_nextgen_ddr3_controller_core.sv
deca_qsys_mem_if_ddr3_emif_c0.v
deca_qsys_mem_if_ddr3_emif_p0_addr_cmd_datapath.v
deca_qsys_mem_if_ddr3_emif_p0_addr_cmd_pads_m10.v
deca_qsys_mem_if_ddr3_emif_p0_clock_pair_generator.v
deca_qsys_mem_if_ddr3_emif_p0_dqdqs_pads_m10.sv
deca_qsys_mem_if_ddr3_emif_p0_flop_mem.v
deca_qsys_mem_if_ddr3_emif_p0_fr_cycle_shifter.v
deca_qsys_mem_if_ddr3_emif_p0_iss_probe.v
deca_qsys_mem_if_ddr3_emif_p0_memphy_m10.sv
deca_qsys_mem_if_ddr3_emif_p0_parameters.tcl
deca_qsys_mem_if_ddr3_emif_p0_pin_assignments.tcl
deca_qsys_mem_if_ddr3_emif_p0_pin_map.tcl
deca_qsys_mem_if_ddr3_emif_p0.ppf
deca_qsys_mem_if_ddr3_emif_p0_read_datapath_m10.sv
deca_qsys_mem_if_ddr3_emif_p0_read_valid_selector.v
deca_qsys_mem_if_ddr3_emif_p0_report_timing_core.tcl
deca_qsys_mem_if_ddr3_emif_p0_report_timing.tcl
deca_qsys_mem_if_ddr3_emif_p0_reset_m10.v
deca_qsys_mem_if_ddr3_emif_p0_reset_sync.v
deca_qsys_mem_if_ddr3_emif_p0.sdc
deca_qsys_mem_if_ddr3_emif_p0_simple_ddio_out_m10.sv
deca_qsys_mem_if_ddr3_emif_p0.sv
deca_qsys_mem_if_ddr3_emif_p0_timing.tcl
deca_qsys_mem_if_ddr3_emif_p0_write_datapath_m10.v
deca_qsys_mem_if_ddr3_emif_pll0.sv
deca_qsys_mem_if_ddr3_emif_s0_AC_ROM.hex
deca_qsys_mem_if_ddr3_emif_s0_inst_ROM.hex
deca_qsys_mem_if_ddr3_emif_s0_make_qsys_seq.tcl
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_avalon_st_adapter_error_adapter_0.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_avalon_st_adapter.v
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_cmd_demux.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_cmd_mux.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_router_001.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_router.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_rsp_demux.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0_rsp_mux.sv
deca_qsys_mem_if_ddr3_emif_s0_mm_interconnect_0.v
deca_qsys_mem_if_ddr3_emif_s0.v
deca_qsys_mem_if_ddr3_emif.v
max10emif_dcfifo.sv
rw_manager_ac_ROM_reg.v
rw_manager_bitcheck.v
rw_manager_core.sv
rw_manager_data_broadcast.v
rw_manager_data_decoder.v
rw_manager_datamux.v
rw_manager_ddr3.v
rw_manager_di_buffer.v
rw_manager_di_buffer_wrap.v
rw_manager_dm_decoder.v
rw_manager_generic.sv
rw_manager_inst_ROM_reg.v
rw_manager_jumplogic.v
rw_manager_lfsr12.v
rw_manager_lfsr36.v
rw_manager_lfsr72.v
rw_manager_m10_ac_ROM.v
rw_manager_m10_inst_ROM.v
rw_manager_pattern_fifo.v
rw_manager_ram_csr.v
rw_manager_ram.v
rw_manager_read_datapath.v
rw_manager_write_decoder.v
sequencer_m10.sv
sequencer_phy_mgr.sv
sequencer_pll_mgr.sv
"

rm -rf "$DEST"
mkdir -p "$DEST"
MISSING=0
while read -r f; do
    [ -n "$f" ] || continue
    if [ -f "$SUB/$f" ]; then
        cp "$SUB/$f" "$DEST/"
    else
        echo "ERROR: generated output missing: $f"
        MISSING=1
    fi
done <<< "$FILES"
if [ "$MISSING" -ne 0 ]; then
    echo "ERROR: incomplete generation — leaving $DEST untouched is not possible; re-run after fixing."
    exit 1
fi
N=$(ls "$DEST" | wc -l)
echo "Copied $N files to $DEST"
[ "$N" -eq 130 ] || { echo "WARNING: expected 130 files, got $N — diff the directory against a known-good copy."; }

# ── Optional: standalone synthesis smoke test ──────────────────────────────
# Verifies the regenerated set elaborates cleanly with quartus_map on the
# MAX10 10M50DAF484C6GES (catches missing module dependencies).
if [ "$SMOKE_TEST" -eq 1 ]; then
    command -v quartus_map >/dev/null 2>&1 || { echo "ERROR: quartus_map not found for smoke test."; exit 1; }
    SMOKE="$(mktemp -d)"
    trap 'rm -rf "$WORK" "$SMOKE"' EXIT
    cat > "$SMOKE/emif_top.v" <<'EOF'
module emif_top (
    input  pll_ref_clk,
    input  global_reset_n,
    output wire [14:0] mem_a,
    output wire [2:0]  mem_ba,
    inout  wire mem_ck,
    inout  wire mem_ck_n,
    output wire mem_cke,
    output wire mem_cs_n,
    output wire [1:0]  mem_dm,
    output wire mem_ras_n,
    output wire mem_cas_n,
    output wire mem_we_n,
    output wire mem_reset_n,
    inout  wire [15:0] mem_dq,
    inout  wire [1:0]  mem_dqs,
    inout  wire [1:0]  mem_dqs_n,
    output wire mem_odt,
    output wire afi_clk,
    output wire afi_half_clk,
    output wire avl_ready,
    input  wire avl_burstbegin,
    input  wire [25:0] avl_addr,
    output wire avl_rdata_valid,
    output wire [63:0] avl_rdata,
    input  wire [63:0] avl_wdata,
    input  wire [7:0]  avl_be,
    input  wire avl_read_req,
    input  wire avl_write_req,
    input  wire [2:0]  avl_size,
    output wire local_init_done,
    output wire local_cal_success,
    output wire local_cal_fail
);
deca_qsys_mem_if_ddr3_emif emif (
    .pll_ref_clk(pll_ref_clk),
    .global_reset_n(global_reset_n),
    .soft_reset_n(1'b1),
    .afi_clk(afi_clk),
    .afi_half_clk(afi_half_clk),
    .afi_reset_n(),
    .afi_reset_export_n(),
    .mem_a(mem_a), .mem_ba(mem_ba), .mem_ck(mem_ck), .mem_ck_n(mem_ck_n),
    .mem_cke(mem_cke), .mem_cs_n(mem_cs_n), .mem_dm(mem_dm),
    .mem_ras_n(mem_ras_n), .mem_cas_n(mem_cas_n), .mem_we_n(mem_we_n),
    .mem_reset_n(mem_reset_n), .mem_dq(mem_dq), .mem_dqs(mem_dqs),
    .mem_dqs_n(mem_dqs_n), .mem_odt(mem_odt),
    .avl_ready(avl_ready), .avl_burstbegin(avl_burstbegin), .avl_addr(avl_addr),
    .avl_rdata_valid(avl_rdata_valid), .avl_rdata(avl_rdata),
    .avl_wdata(avl_wdata), .avl_be(avl_be), .avl_read_req(avl_read_req),
    .avl_write_req(avl_write_req), .avl_size(avl_size),
    .local_init_done(local_init_done), .local_cal_success(local_cal_success),
    .local_cal_fail(local_cal_fail),
    .pll_mem_clk(), .pll_write_clk(), .pll_locked(),
    .pll_capture0_clk(), .pll_capture1_clk()
);
endmodule
EOF
    {
        echo 'set_global_assignment -name FAMILY "MAX 10"'
        echo 'set_global_assignment -name DEVICE 10M50DAF484C6GES'
        echo 'set_global_assignment -name TOP_LEVEL_ENTITY emif_top'
        echo 'set_global_assignment -name VERILOG_INPUT_VERSION SYSTEMVERILOG_2005'
        for f in "$DEST"/*.v "$DEST"/*.sv; do
            echo "set_global_assignment -name VERILOG_FILE $f"
        done
    } > "$SMOKE/emif_test.qsf"
    echo "Smoke test: quartus_map emif_test"
    if ( cd "$SMOKE" && quartus_map emif_test --read_settings_files=on 2>&1 | tail -3 ); then
        echo "Smoke test PASSED."
    else
        echo "Smoke test FAILED — see log above."
        exit 1
    fi
fi

echo "Done."
