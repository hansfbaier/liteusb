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

    /* Clear BSS (.bss) */
    la      t0, __bss_start
    la      t1, __bss_end
1:
    bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:

    /* Call main; never returns */
    call    main

    /* If main returns, loop forever */
3:
    j       3b

    .size   _start, . - _start
