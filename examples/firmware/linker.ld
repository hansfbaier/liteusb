/*
 * DECA EHCI Keyboard Host — VexRiscv bare-metal linker script.
 *
 * Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Generated using DeepSeek V4.0 Pro
 *
 * Memory layout (typical LiteX VexRiscv with internal SRAM):
 *   RAM: 0x00000000 - 0x0000FFFF   (64 KB internal SRAM)
 *
 * The EHCI DMA pool (.bss.ehci_dma) is placed at a fixed offset with
 * 4K alignment so the frame list and QH/qTD structures satisfy the
 * EHCI alignment requirements.
 */

MEMORY
{
    /* Internal SRAM (adjust size to match your SoC --with-ram) */
    RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 0x10000  /* 64 KB */
}

ENTRY(_start)

PHDRS
{
    text PT_LOAD FLAGS(5);   /* R-X */
    data PT_LOAD FLAGS(6);   /* RW- */
}

SECTIONS
{
    .text : {
        *(.text._start)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > RAM :text

    .data : {
        *(.data*)
        . = ALIGN(4);
    } > RAM :data

    .bss (NOLOAD) : {
        . = ALIGN(4);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM

    /* EHCI DMA structures: 4K-aligned frame list first.
     * Keep this region away from the stack (which grows down from
     * __stack_top). */
    .bss.ehci_dma (NOLOAD) : {
        . = ALIGN(4096);
        __ehci_dma_start = .;
        KEEP(*(.bss.ehci_dma))
        . = ALIGN(4096);
        __ehci_dma_end = .;
    } > RAM

    . = ALIGN(16);
    __stack_top = . + 0x2000;   /* 8 KB stack */

    _end = .;
}
