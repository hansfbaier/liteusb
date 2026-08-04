/*
 * DECA EHCI Keyboard Host — bare-metal USB host firmware.
 *
 * Enumerates a USB HID keyboard on the liteusb EHCI host controller
 * and displays each pressed keycode in binary on the DECA LEDs.
 *
 * Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Generated using DeepSeek V4.0 Pro
 *
 * Architecture
 * ------------
 *   main() ──► ehci_init()          reset + configure the host controller
 *          ──► port_reset()         assert bus reset, wait for enable
 *          ──► enumerate_keyboard() full USB device enumeration
 *          ──► poll loop            interrupt-IN endpoint polling,
 *                                   HID report decode, LED output
 *
 * The EHCI controller executes transfers from in-RAM data structures
 * (frame list, queue heads, transfer descriptors).  This firmware
 * maintains those structures per the EHCI Rev 1.0 specification.
 */

#include <stdint.h>
#include <stddef.h>

#include "ehci.h"

/* ── Register access (memory-mapped, 32-bit little-endian) ─────────────── */

static inline uint32_t ehci_read(uint32_t off) {
    return *(volatile uint32_t *)(EHCI_BASE + off);
}
static inline void ehci_write(uint32_t off, uint32_t val) {
    *(volatile uint32_t *)(EHCI_BASE + off) = val;
}

/* ── LED output (LiteX CSR register, active-low LEDs) ──────────────────── */

static inline void leds_set(uint8_t value) {
    *(volatile uint32_t *)LED_OUT_BASE = value;  /* gateware inverts */
}

/* ── UART debug output via LiteX CSR (JTAG UART at default base) ───────── */

#define UART_BASE   0xf0001000  /* jtag_uart — adjust from csr.csv */
static inline void uart_putc(char c) {
    volatile uint32_t *txfull = (volatile uint32_t *)(UART_BASE + 0x04);
    volatile uint32_t *txdata = (volatile uint32_t *)(UART_BASE + 0x00);
    while (*txfull & 1) { }
    *txdata = c;
}
static void uart_puts(const char *s) {
    while (*s) uart_putc(*s++);
}
static void uart_hex(uint32_t v) {
    static const char hx[] = "0123456789ABCDEF";
    char buf[11]; int i = 10;
    buf[10] = '\0';
    do { buf[--i] = hx[v & 0xF]; v >>= 4; } while (v);
    uart_puts(&buf[i]);
}

/* ── EHCI in-RAM data structures ───────────────────────────────────────── */

/* 4K-aligned frame list (1024 entries), plus QH/qTD pools.
 * Alignment is guaranteed by the linker script (.bss.ehci_dma section). */
static uint8_t      ehci_pool[0x8000] __attribute__((aligned(4096)));
static uint32_t    *frame_list;   /* 1024 x 32-bit */
static ehci_qh_t   *ctrl_qh;      /* control pipe QH */
static ehci_qh_t   *int_qh;       /* interrupt pipe QH */
static ehci_qtd_t  *int_qtd;      /* interrupt transfer qTD */

/* USB keyboard state */
static uint8_t  kbd_addr = 0;         /* assigned device address */
static uint8_t  kbd_ep0_maxp = 8;     /* EP0 max packet (from device desc) */
static uint8_t  kbd_ep_in = 0;        /* interrupt IN endpoint number */
static uint8_t  kbd_max_packet = 8;   /* boot protocol report size */
static uint16_t kbd_poll_interval = 8;/* ms between polls */
static uint8_t  report[8];            /* last HID report */

/* ── Small helpers ──────────────────────────────────────────────────────── */

static void delay_ms(uint32_t ms) {
    volatile uint32_t i;
    while (ms--) { for (i = 0; i < 40000; i++) { __asm__ volatile(""); } }
}

/* ── EHCI initialization ───────────────────────────────────────────────── */

static void ehci_init(void) {
    uint32_t sts;

    uart_puts("EHCI init\r\n");

    /* 1. Halt the controller if running */
    if (ehci_read(EHCI_USBCMD) & USBCMD_RUN) {
        ehci_write(EHCI_USBCMD, 0);
        for (int i = 0; i < 100; i++) {
            if (ehci_read(EHCI_USBSTS) & USBSTS_HCH) break;
        }
    }

    /* 2. Host Controller Reset */
    ehci_write(EHCI_USBCMD, USBCMD_HCRESET);
    do {
        sts = ehci_read(EHCI_USBCMD);
    } while (sts & USBCMD_HCRESET);

    /* 3. Program frame list (4K-aligned) */
    frame_list = (uint32_t *)ehci_pool;
    for (int i = 0; i < EHCI_FRAME_LIST_ENTRIES; i++) {
        frame_list[i] = EHCI_FL_T_BIT;  /* terminated (empty) */
    }

    /* 4. Set up data structure pointers */
    ehci_write(EHCI_PERIODICLISTBASE, (uint32_t)frame_list);
    ehci_write(EHCI_ASYNCLISTADDR, 0);  /* empty async list for now */

    /* 5. Configure flag: route all ports to this controller */
    ehci_write(EHCI_CONFIGFLAG, 1);

    /* 6. Run */
    ehci_write(EHCI_USBCMD, USBCMD_RUN);
    delay_ms(10);

    /* 7. Clear any stale status */
    ehci_write(EHCI_USBSTS, ehci_read(EHCI_USBSTS) & 0x3F);
    uart_puts("EHCI running\r\n");
}

/* ── Port reset ────────────────────────────────────────────────────────── */

static int port_reset(void) {
    uint32_t portsc;

    uart_puts("Port reset\r\n");

    /* Wait for a device to be connected */
    for (int i = 0; i < 500; i++) {
        portsc = ehci_read(EHCI_PORTSC1);
        if (portsc & PORTSC_CCS) break;
        delay_ms(10);
    }
    if (!(portsc & PORTSC_CCS)) {
        uart_puts("No device connected\r\n");
        return -1;
    }
    leds_set(0x00);  /* off */

    /* Assert port reset (min 10 ms per spec, we drive 50 ms) */
    portsc = ehci_read(EHCI_PORTSC1);
    ehci_write(EHCI_PORTSC1, portsc | PORTSC_PR);
    delay_ms(50);
    portsc = ehci_read(EHCI_PORTSC1);
    ehci_write(EHCI_PORTSC1, portsc & ~PORTSC_PR);
    delay_ms(10);

    /* Wait for port enable (device chirp completed, speed detected) */
    for (int i = 0; i < 500; i++) {
        portsc = ehci_read(EHCI_PORTSC1);
        if (portsc & PORTSC_PE) break;
        delay_ms(10);
    }
    if (!(portsc & PORTSC_PE)) {
        uart_puts("Port not enabled\r\n");
        return -1;
    }

    uart_puts("Port enabled, line state: ");
    if ((portsc & PORTSC_LINE_STATUS_MASK) == PORTSC_LINE_STATUS_K)
        uart_puts("K (LS)\r\n");
    else
        uart_puts("J (FS/HS)\r\n");
    return 0;
}

/* ── qTD/QH helpers ────────────────────────────────────────────────────── */

static void qtd_setup(ehci_qtd_t *qtd, uint32_t pid, uint8_t *buf,
                      uint16_t bytes, uint8_t toggle) {
    qtd->next_qtd = QTD_NEXT_QTD_TERMINATE;
    qtd->alt_qtd  = QTD_NEXT_QTD_TERMINATE;
    qtd->buf[0] = (uint32_t)buf;
    for (int i = 1; i < 5; i++) qtd->buf[i] = 0;
    qtd->token = QTD_TOKEN_ACTIVE
               | (3 << 10)                 /* CERR = 3 (max retries) */
               | pid
               | ((uint32_t)toggle << 31)  /* data toggle on error */
               | ((uint32_t)bytes << QTD_TOKEN_BYTES_SHIFT);
}

static void qh_init_ctrl(ehci_qh_t *qh) {
    qh->hlp     = QH_LINK_TERMINATE;
    qh->ep_char = (0 << 12)   /* EPS = FS (bits 13:12; keyboard is FS) */
                | (8 << 16)   /* MaxPacketLength = 8 (bits 26:16) */
                | 0;          /* device address 0, EP 0 */
    qh->ep_cap  = 0;
    qh->cur_qtd = 0;
    for (int i = 0; i < 8; i++) qh->overlay[i] = 0;
}

/* Initialize the interrupt-IN QH for the polled keyboard endpoint */
static void qh_init_int(ehci_qh_t *qh, uint8_t addr, uint8_t ep,
                        uint8_t max_packet) {
    qh->hlp     = QH_LINK_TERMINATE;   /* end of the periodic chain */
    qh->ep_char = (0 << 12)                    /* EPS = FS */
                | ((uint32_t)max_packet << 16) /* MaxPacketLength */
                | ((uint32_t)ep << 8)          /* EndPt */
                | addr;                        /* DevAddr */
    qh->ep_cap  = 0;
    qh->cur_qtd = 0;
    for (int i = 0; i < 8; i++) qh->overlay[i] = 0;
}

/* ── Async control transfer (control pipe) ─────────────────────────────── */

static int control_transfer(uint8_t bmRequestType, uint8_t bRequest,
                            uint16_t wValue, uint16_t wIndex,
                            uint16_t wLength, uint8_t *data,
                            uint8_t *toggle) {
    /* 8-byte setup packet */
    uint8_t setup[8];
    setup[0] = bmRequestType; setup[1] = bRequest;
    setup[2] = wValue & 0xFF;  setup[3] = wValue >> 8;
    setup[4] = wIndex & 0xFF;  setup[5] = wIndex >> 8;
    setup[6] = wLength & 0xFF; setup[7] = wLength >> 8;

    /* Prepare 3 qTDs: SETUP, (DATA), STATUS (carved from the DMA pool) */
    ehci_qtd_t *qtd_s = (ehci_qtd_t *)(ehci_pool + 0x100);
    ehci_qtd_t *qtd_d = (ehci_qtd_t *)(ehci_pool + 0x200);
    ehci_qtd_t *qtd_t = (ehci_qtd_t *)(ehci_pool + 0x300);

    /* SETUP stage: 8 bytes, DATA0 (a SETUP always resets the toggle) */
    qtd_setup(qtd_s, QTD_TOKEN_PID_SETUP, setup, 8, 0);
    qtd_s->next_qtd = (uint32_t)qtd_d;

    /* After a SETUP, the DATA stage always starts at DATA1 (USB 2.0
     * §8.5.3); track the toggle locally from there. */
    *toggle = 1;

    if (wLength) {
        /* DATA stage */
        if (bmRequestType & 0x80) {
            qtd_setup(qtd_d, QTD_TOKEN_PID_IN, data, wLength, *toggle);
        } else {
            qtd_setup(qtd_d, QTD_TOKEN_PID_OUT, data, wLength, *toggle);
        }
        qtd_d->next_qtd = (uint32_t)qtd_t;
        /* STATUS stage: opposite direction, DATA1 */
        if (bmRequestType & 0x80) {
            qtd_setup(qtd_t, QTD_TOKEN_PID_OUT, 0, 0, 1);
        } else {
            qtd_setup(qtd_t, QTD_TOKEN_PID_IN, 0, 0, 1);
        }
    } else {
        /* No DATA stage: STATUS is DATA1, opposite of SETUP direction */
        if (bmRequestType & 0x80) {
            qtd_setup(qtd_t, QTD_TOKEN_PID_OUT, 0, 0, 1);
        } else {
            qtd_setup(qtd_t, QTD_TOKEN_PID_IN, 0, 0, 1);
        }
        qtd_s->next_qtd = (uint32_t)qtd_t;
    }
    qtd_t->next_qtd = QTD_NEXT_QTD_TERMINATE;

    /* Set up control QH pointing at the first qTD */
    qh_init_ctrl(ctrl_qh);
    ctrl_qh->ep_char = (0 << 12)                    /* EPS = FS */
                     | ((uint32_t)kbd_ep0_maxp << 16)
                     | (kbd_addr << 0);
    ctrl_qh->cur_qtd = (uint32_t)qtd_s;

    /* Link into async list: ASYNCLISTADDR -> QH */
    ehci_write(EHCI_ASYNCLISTADDR, (uint32_t)ctrl_qh);
    ehci_write(EHCI_USBCMD, ehci_read(EHCI_USBCMD) | USBCMD_ASE);

    /* Wait for the transfer to complete (Active bit clears) */
    int timeout = 10000;
    while (timeout--) {
        if (!(qtd_t->token & QTD_TOKEN_ACTIVE)) break;
    }

    if (qtd_t->token & QTD_TOKEN_ACTIVE) {
        uart_puts("CTRL timeout\r\n");
        return -1;
    }
    if (qtd_t->token & QTD_TOKEN_HALTED) {
        uart_puts("CTRL halt\r\n");
        return -1;
    }

    /* Update data toggle */
    *toggle = (*toggle) ^ 1;
    return 0;
}

/* ── USB enumeration ───────────────────────────────────────────────────── */

static int get_device_descriptor(uint8_t addr, uint8_t *buf, uint16_t len) {
    uint8_t tog = 0;
    (void)addr;  /* address tracked in kbd_addr by set_address() */
    return control_transfer(0x80, 6, 1, 0, len, buf, &tog);
}

static int set_address(uint8_t addr) {
    uint8_t tog = 0;
    int r = control_transfer(0x00, 5, addr, 0, 0, 0, &tog);
    if (r == 0) kbd_addr = addr;
    return r;
}

static int get_config_descriptor(uint8_t *buf, uint16_t len) {
    uint8_t tog = 0;
    return control_transfer(0x80, 6, 2, 0, len, buf, &tog);
}

static int set_configuration(uint8_t cfg) {
    uint8_t tog = 0;
    return control_transfer(0x00, 9, cfg, 0, 0, 0, &tog);
}

/* HID class request: SET_PROTOCOL (boot protocol = 0) */
static int set_boot_protocol(void) {
    uint8_t tog = 0;
    return control_transfer(0x21, 0x0B, 0, 0, 0, 0, &tog);
}

/* ── Interrupt-IN endpoint polling (periodic schedule) ─────────────────── */

static int poll_keyboard(void) {
    ehci_qtd_t *qtd = int_qtd;

    /* Reprime the qTD (Active) and point the QH at it */
    qtd_setup(qtd, QTD_TOKEN_PID_IN, report, kbd_max_packet, 1);
    qh_init_int(int_qh, kbd_addr, kbd_ep_in, kbd_max_packet);
    int_qh->cur_qtd = (uint32_t)qtd;

    /* Link the interrupt QH into the periodic schedule.
     * Every frame-list entry points to it (polled every microframe). */
    for (int i = 0; i < EHCI_FRAME_LIST_ENTRIES; i++) {
        frame_list[i] = (uint32_t)int_qh;  /* QH link, type = QH */
    }

    /* Enable periodic schedule */
    ehci_write(EHCI_USBCMD, ehci_read(EHCI_USBCMD) | USBCMD_PSE);

    /* Wait for completion (poll interval ms * 1000 iterations/ms) */
    int timeout = 100000;
    while (timeout--) {
        if (!(qtd->token & QTD_TOKEN_ACTIVE)) break;
        if (qtd->token & (QTD_TOKEN_HALTED | QTD_TOKEN_XACTERR |
                          QTD_TOKEN_DATABUFFER | QTD_TOKEN_BABBLE)) break;
    }
    if (timeout <= 0) {
        qtd->token = 0;
        return -1;
    }
    if (qtd->token & (QTD_TOKEN_HALTED | QTD_TOKEN_XACTERR |
                      QTD_TOKEN_DATABUFFER | QTD_TOKEN_BABBLE)) {
        uart_puts("EP err\r\n");
        qtd->token = 0;
        return -1;
    }
    return 0;
}

/* ── HID report decode ─────────────────────────────────────────────────── */

static void handle_report(void) {
    /* Boot protocol keyboard report:
     *   byte 0: modifier keys
     *   byte 1: reserved
     *   bytes 2-7: keycodes (0 = no key)
     */
    uint8_t kc = report[2];
    if (kc != 0) {
        uart_puts("Key: 0x");
        uart_hex(kc);
        uart_puts(" LED: 0x");
        uart_hex(kc);
        uart_puts("\r\n");
        leds_set(kc);  /* binary keycode on LEDs */
    }
}

/* ── Enumeration sequence ──────────────────────────────────────────────── */

static int enumerate_keyboard(void) {
    uint8_t dev_desc[18];
    uint8_t cfg_desc[64];

    uart_puts("Enumerate\r\n");

    /* 1. Get first 8 bytes of device descriptor (max packet size) */
    if (get_device_descriptor(0, dev_desc, 8)) {
        uart_puts("Get desc 8 fail\r\n");
        return -1;
    }
    uart_puts("bMaxPacketSize0: ");
    uart_hex(dev_desc[7]);
    uart_puts("\r\n");
    kbd_ep0_maxp = dev_desc[7];

    /* 2. Assign address 1 */
    if (set_address(1)) {
        uart_puts("Set addr fail\r\n");
        return -1;
    }
    uart_puts("Address 1 assigned\r\n");

    /* 3. Get full device descriptor (18 bytes) */
    if (get_device_descriptor(1, dev_desc, 18)) {
        uart_puts("Get desc 18 fail\r\n");
        return -1;
    }

    /* 4. Get configuration descriptor (64 bytes covers typical) */
    if (get_config_descriptor(cfg_desc, 64)) {
        uart_puts("Get cfg fail\r\n");
        return -1;
    }

    /* 5. Parse config descriptor: find HID keyboard interface + EP */
    uint8_t *p = cfg_desc;
    int found = 0;
    while (p < cfg_desc + 64 && p[0] >= 2) {
        if (p[1] == 4) {  /* interface descriptor */
            if (p[5] == 3 && p[6] == 1 && p[7] == 1) {
                /* class=3 (HID), subclass=1 (boot), protocol=1 (keyboard) */
                found = 1;
                uart_puts("Found HID keyboard iface\r\n");
            }
        } else if (p[1] == 5 && found) {  /* endpoint descriptor */
            if ((p[2] & 0x80) && (p[3] == 3)) {  /* interrupt IN */
                kbd_ep_in = p[2] & 0x0F;
                kbd_max_packet = p[4];
                kbd_poll_interval = p[5];
                uart_puts("EP IN: ");
                uart_hex(kbd_ep_in);
                uart_puts(" maxp: ");
                uart_hex(kbd_max_packet);
                uart_puts("\r\n");
                break;
            }
        }
        p += p[0];
    }
    if (kbd_ep_in == 0) {
        uart_puts("No keyboard endpoint found\r\n");
        return -1;
    }

    /* 6. Set configuration 1 */
    if (set_configuration(1)) {
        uart_puts("Set cfg fail\r\n");
        return -1;
    }

    /* 7. Switch keyboard to boot protocol */
    if (set_boot_protocol()) {
        uart_puts("Boot proto fail\r\n");
        return -1;
    }

    uart_puts("Keyboard ready\r\n");
    leds_set(0xFF);  /* flash all LEDs on ready */
    delay_ms(200);
    leds_set(0x00);
    return 0;
}

/* ── Main ──────────────────────────────────────────────────────────────── */

int main(void) {
    uart_puts("\r\nDECA EHCI Keyboard Host\r\n");

    /* Carve the DMA pool: frame list first, then QH/qTD */
    frame_list = (uint32_t *)ehci_pool;                     /* 0x0000 */
    ctrl_qh    = (ehci_qh_t   *)(ehci_pool + 0x1000);       /* 0x1000 */
    int_qh     = (ehci_qh_t   *)(ehci_pool + 0x2000);       /* 0x2000 */
    int_qtd    = (ehci_qtd_t  *)(ehci_pool + 0x2100);       /* 0x2100 */

    ehci_init();

    while (1) {
        if (port_reset() == 0) {
            if (enumerate_keyboard() == 0) {
                /* Main polling loop: display keycodes on LEDs */
                while (1) {
                    if (poll_keyboard() == 0) {
                        handle_report();
                    }
                    delay_ms(1);
                }
            }
        }
        /* Retry: device may have been disconnected */
        delay_ms(500);
    }
}
