/* DECA EHCI Keyboard Host — VexRiscv startup code (crt0).
 *
 * Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Generated using DeepSeek V4.0 Pro
 *
 * Minimal RV32IM startup: clear BSS, set up stack, call main.
 */

    .section .text._start
    .global _start
    .type   _start, @function

_start:
    /* Set up stack */
    la      sp, __stack_top

    /* Copy .data from ROM (LMA) to RAM (VMA) */
    la      t0, __data_load
    la      t1, __data_start
    la      t2, __data_end
1:
    bgeu    t1, t2, 2f
    lw      t3, 0(t0)
    sw      t3, 0(t1)
    addi    t0, t0, 4
    addi    t1, t1, 4
    j       1b
2:

    /* Clear BSS (.bss) */
    la      t0, __bss_start
    la      t1, __bss_end
3:
    bgeu    t0, t1, 4f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       3b
4:

    /* Call main; never returns */
    call    main

    /* If main returns, loop forever */
3:
    j       3b

    .size   _start, . - _start
