#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Terasic DECA: LiteX SoC (VexRiscv + BIOS) with console over USB CDC-ACM.

A regular LiteX SoC whose console UART is tunneled through a LiteUSB
CDC-ACM device: plug the DECA's ULPI USB port into a PC and open the
virtual COM port (e.g. ``litex_term /dev/ttyACM0``) to reach the BIOS.

Architecture notes:
  - ``uart_name="stream"`` gives a UART with bare stream endpoints, which
    are wired to the ACM device's sink/source streams.
  - The whole design runs on the 60MHz usb clock (``sys_from_usb=True``):
    ACM FIFOs are sys-clocked but feed usb-domain endpoints, so sys and usb
    must be the same clock net (like the LUNA DECA designs).
  - ``add_auto_tx_flush`` keeps the BIOS from stalling when no host is
    reading the console.

Build & load:
    python3 terasic_deca_acm_console.py --build [--load]
Then:
    ls /dev/ttyACM* && litex_term /dev/ttyACM0
"""

from migen import *

from litex.soc.cores.uart import UART

from liteusb.gateware.usb.devices.acm import USBACMSerialDevice

from terasic_deca_common import DecaUSBSoC, deca_main

# SoC -----------------------------------------------------------------------

class ACMConsoleSoC(DecaUSBSoC):
    """DECA SoC with VexRiscv CPU, BIOS and USB CDC-ACM console."""

    def __init__(self, *args, **kwargs):
        # Console UART is created manually (wired to the ACM device).
        kwargs["with_uart"]  = False
        # Whole design on the 60MHz usb clock (single clock net, no CDC).
        kwargs["sys_from_usb"]  = True
        kwargs["sys_clk_freq"]  = 60e6
        # Default CPU: VexRiscv (still overridable on the command line).
        if kwargs.get("cpu_type") in [None, "None"]:
            kwargs["cpu_type"] = "vexriscv"
            # The parser sized the SoC for CPU-less (no ROM); restore the ROM
            # needed for the BIOS when a CPU is forced in afterwards.
            if kwargs.get("integrated_rom_size") is None:
                kwargs["integrated_rom_size"] = 128*1024
        super().__init__(*args, **kwargs)

    def add_usb_device(self, ulpi):
        # CDC-ACM USB device (virtual COM port).
        self.submodules.acm = acm = USBACMSerialDevice(
            bus             = ulpi,
            idVendor        = 0x1209,
            idProduct          = 0x0008,
            handle_clocking = False,
        )
        self.usb = acm.usb
        self.comb += acm.connect.eq(1)

        # Console UART, wired to the ACM streams:
        #   acm.source (from host) -> uart.sink (RX)
        #   uart.source (TX)       -> acm.sink (to host)
        self.uart = uart = UART(
            tx_fifo_depth = 16,
            rx_fifo_depth = 16,
        )
        if self.irq.enabled:
            self.irq.add("uart", use_loc_if_exists=True)
        else:
            self.add_constant("UART_POLLING", check_duplicate=False)

        self.comb += [
            acm.source.connect(uart.sink),
            uart.source.connect(acm.sink),
        ]

        # Flush the TX FIFO when the host is not reading, so console
        # writes cannot stall the CPU (e.g. before the port is opened).
        uart.add_auto_tx_flush(sys_clk_freq=self.sys_clk_freq)

# Build ---------------------------------------------------------------------

def main():
    deca_main(ACMConsoleSoC, "LiteX SoC with USB CDC-ACM console on Terasic DECA")

if __name__ == "__main__":
    main()
