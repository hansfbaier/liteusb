# DECA EHCI Linux SoC

Linux-capable SoC for the Terasic DECA (MAX10 10M50DAF484C6GES):

| Component | Details |
|---|---|
| CPU | VexRiscv-SMP, `linux` variant (MMU, SV32), 1 core @ 75 MHz |
| Memory | 512 MB DDR3L (MT41K256M16HA-125) via Intel EMIF soft controller @ 0x40000000 |
| L2 | 32 KB write-back Wishbone cache (CPU + EHCI-DMA share the same cache ⇒ coherent by construction) |
| USB | liteusb EHCI host controller on the on-board ULPI PHY, CSRs @ 0xe0000000, IRQ 17, DMA master on the CPU coherent DMA bus |
| Console | UART on PMOD P8 (pins 3 = TX, 4 = RX, 3.3 V) |
| Storage | microSD in SPI mode (BIOS `sdcardboot`) |
| EMIF status CSR | `emif_status` — [0]=pll_locked [1]=cal_success [2]=cal_fail [3]=init_done |

Resource usage: **26,204 LEs / 49,760 (53%)**, 17,761 registers, all timing
corners met (worst-case setup slack +0.213 ns; DDR3 PHY paths +0.213 ns).

## Files

- `deca_ehci_linux.py` — the SoC target (`python3 deca_ehci_linux.py --build`)
- `deca_emif.py` — DDR3 EMIF IP wrapper + 32-bit Wishbone bridge
  (single outstanding transaction, 64-bit Avalon slave at afi_clk 150 MHz,
  byte-enable writes, pair cache; holds CPU accesses until `init_done`)
- `vendor/deca_emif/` — Intel EMIF IP sources (from the Terasic DECA CD
  `DECA_DDR3_Nios_Test`, regenerated with Quartus 21.1 qsys-generate)

  **NOT tracked in git**: these are generated portions of the Intel FPGA
  IP, which the Quartus Prime and Intel FPGA IP License Agreement does not
  permit redistributing (see .gitignore). Regenerate locally with:

  ```sh
  ./regenerate_deca_emif.sh /path/to/DECA_DDR3_Nios_Test   # from the DECA CD
  ./regenerate_deca_emif.sh --smoke-test                   # + quartus_map check
  ```

  The script runs `qsys-generate` (Quartus 21.1, Lite Edition works) on the
  Terasic DECA CD demo and extracts the EMIF subset; the optional smoke test
  re-verifies that the set elaborates cleanly for the 10M50DAF484C6GES.
- platform changes in `litex-boards/litex_boards/platforms/terasic_deca.py`:
  added `ddr3_clk` resource (PIN_N15) + `ENABLE_OCT_DONE OFF`

## Clock / timing architecture

- sys 75 MHz: clk50 (M9) → MAX10 PLL
- usb 60 MHz: clk60 (H11, from the ULPI PHY) → MAX10 PLL, phase −120°;
  output also drives the ULPI REFCLK (W3). PLL reset tied low (free-run).
- afi 150 MHz: generated inside the EMIF IP (pll_ref_clk = ddr3_clk PIN_N15);
  the Wishbone↔Avalon bridge crosses sys↔afi with MultiReg CDC (false-pathed)
- ULPI I/O delays per TUSB1210 datasheet (6.14); FPGA→PHY outputs are
  multicycle-2 vs clk600 (the −120° phase makes data target the PHY's
  second sampling edge); DIR→DATA turnaround pin path is false-pathed
  (controller inserts whole-cycle bus turnarounds)

## Building

```sh
source litex-venv/bin/activate
cd liteusb/examples
python3 deca_ehci_linux.py --build --load
```

## Linux boot flow (planned)

1. BIOS `sdcardboot` reads `boot.json` from a FAT-formatted microSD:
   ```json
   {
       "Image":       "0x40000000",
       "rv32.dtb":    "0x40ef0000",
       "rootfs.cpio": "0x41000000",
       "opensbi.bin": "0x40f00000"
   }
   ```
2. BIOS jumps to OpenSBI (`fw_jump.bin`, TEXT_START 0x40F00000,
   FW_JUMP_ADDR 0x40000000 — prebuilt in linux-on-litex-vexriscv/opensbi)
3. OpenSBI boots the kernel (litex-linux fork, Linux-on-LiteX-VexRiscv
   config) with the DTB generated from `csr.json`
4. DTS needs the USB node:
   ```
   usb@e0000000 {
       compatible = "generic-ehci";
       reg = <0xe0000000 0x1000>;
       interrupts = <17>;
   };
   ```
   plus `CONFIG_USB_EHCI_HCD=y` + `CONFIG_USB_EHCI_HCD_PLATFORM=y` in the
   kernel config (the usbhost defconfig currently enables OHCI instead).

## Status

- [x] EMIF DDR3 bring-up (IP extracted from Terasic reference, standalone
      synthesis smoke test, integrated into LiteX)
- [x] Full SoC synthesizes + fits (53%) + ALL timing met
- [ ] Hardware verification (BIOS memtest over DDR3, EHCI on ULPI)
- [ ] Linux software (kernel + EHCI config, rootfs, DTS, SD card images)
