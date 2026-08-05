/*
 * DECA EHCI Keyboard Host — VexRiscv bare-metal linker script.
 *
 * Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Memory layout (LiteX VexRiscv, boot from initialized ROM, no BIOS):
 *   ROM: 0x00000000              (integrated ROM, firmware .text/.rodata)
 *   RAM: 0x40000000              (integrated main RAM: .data/.bss/
 *                                 EHCI DMA pool/stack)
 *
 * The EHCI DMA pool (.bss.ehci_dma) is 4K-aligned so the frame list and
 * QH/qTD structures satisfy the EHCI alignment requirements.
 */

MEMORY
{
    ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 0x2000   /* 8 KB */
    RAM (rwx) : ORIGIN = 0x40000000, LENGTH = 0x8000   /* 32 KB */
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
    } > ROM :text

    /* .data runs from RAM but is loaded in ROM right after .text;
     * crt0 copies it to RAM at boot. */
    .data : {
        __data_load = LOADADDR(.data);
        __data_start = .;
        *(.data*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT> ROM :data

    .bss (NOLOAD) : {
        . = ALIGN(4);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM

    /* EHCI DMA structures: 4K-aligned frame list first. */
    .bss.ehci_dma (NOLOAD) : {
        . = ALIGN(4096);
        __ehci_dma_start = .;
        KEEP(*(.bss.ehci_dma))
        . = ALIGN(4096);
        __ehci_dma_end = .;
    } > RAM

    . = ALIGN(16);
    __stack_top = . + 0x2000;   /* 8 KB stack */

    /* Stack must stay inside the 32 KiB RAM (0x40000000-0x40008000). */
    ASSERT(__stack_top <= 0x40008000, "stack overflows 32 KiB RAM")

    _end = .;
}
