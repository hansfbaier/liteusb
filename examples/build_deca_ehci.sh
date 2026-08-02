#!/usr/bin/env bash
#
# DECA EHCI Keyboard Host — build + load orchestration.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro
#
# Steps:
#   1. Build the LiteX gateware (Quartus for the DECA MAX10)
#   2. Build the bare-metal RISC-V firmware
#   3. Load the bitstream to the DECA
#   4. (Optional) open a serial terminal for debug output
#
# Prerequisites:
#   - LiteX toolchain with Quartus (for MAX10)
#   - riscv64-unknown-elf-gcc (or the LiteX RISC-V toolchain)
#   - openFPGALoader or USB-Blaster driver for programming
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIRMWARE_DIR="$SCRIPT_DIR/firmware"

# ── Config ────────────────────────────────────────────────────────────────

BUILD_GATEWARE=1
BUILD_FIRMWARE=1
LOAD_BITSTREAM=0
OPEN_TERMINAL=0
TARGET="${TARGET:-deca_ehci_host}"

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --no-gateware      Skip the LiteX/Quartus build
  --no-firmware      Skip the firmware build
  --load             Load the bitstream to the DECA after building
  --term             Open litex_term after loading (JTAG UART)
  -h, --help         Show this help
EOF
}

for arg in "$@"; do
    case "$arg" in
        --no-gateware) BUILD_GATEWARE=0 ;;
        --no-firmware) BUILD_FIRMWARE=0 ;;
        --load)        LOAD_BITSTREAM=1 ;;
        --term)        OPEN_TERMINAL=1 ;;
        -h|--help)     usage; exit 0 ;;
        *) echo "Unknown option: $arg"; usage; exit 1 ;;
    esac
done

# ── 1. Gateware ───────────────────────────────────────────────────────────

if [ "$BUILD_GATEWARE" = "1" ]; then
    echo "=== Building LiteX gateware ($TARGET) ==="
    # The SoC builder is a liteusb example; run from examples/ so the
    # terasic_deca_common import resolves.
    (cd "$SCRIPT_DIR" && \
     python3 deca_ehci_host.py --build \
       --cpu-type vexriscv \
       --uart-name jtag_uart)
fi

# ── 2. Firmware ───────────────────────────────────────────────────────────

if [ "$BUILD_FIRMWARE" = "1" ]; then
    echo "=== Building bare-metal firmware ==="
    make -C "$FIRMWARE_DIR"
fi

# ── 3. Load ───────────────────────────────────────────────────────────────

if [ "$LOAD_BITSTREAM" = "1" ]; then
    BITSTREAM="$(ls "$SCRIPT_DIR/build/$TARGET/gateware/"*.rbf 2>/dev/null || true)"
    if [ -z "$BITSTREAM" ]; then
        BITSTREAM="$(ls "$SCRIPT_DIR/build/$TARGET/gateware/"*.sof 2>/dev/null || true)"
    fi
    if [ -z "$BITSTREAM" ]; then
        echo "ERROR: no bitstream found under build/$TARGET/gateware/"
        exit 1
    fi
    echo "=== Loading bitstream: $BITSTREAM ==="
    openFPGALoader -b terasic_deca "$BITSTREAM"
fi

# ── 4. Terminal ───────────────────────────────────────────────────────────

if [ "$OPEN_TERMINAL" = "1" ]; then
    echo "=== Opening JTAG UART terminal (Ctrl+A X to exit) ==="
    # Find the JTAG UART TTY (typically /dev/ttyUSB0 with USB-Blaster)
    TTY="${TTY:-/dev/ttyUSB0}"
    litex_term "$TTY"
fi

echo "=== Done ==="
echo "To load the firmware over the terminal: use the BIOS 'load' command"
echo "with serial boot (or mount the RISC-V toolchain's firmware at 0x00000000)."
