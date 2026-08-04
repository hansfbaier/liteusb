/*
 * EHCI host controller register and data structure definitions
 *
 * Translated from liteusb/liteusb/gateware/usb/usb2/host/data_structures.py
 * (LiteUSB, BSD-3-Clause).
 *
 * Enhanced Host Controller Interface Specification for Universal Serial
 * Bus, Rev 1.0 — Section 3: Data Structures, and the operational register
 * map. All structures describe the 32-bit little-endian DWORD layouts used
 * by the EHCI host controller.
 */
#ifndef EHCI_H
#define EHCI_H

#include <stdint.h>

/* ── Base addresses ───────────────────────────────────────────────────────── */

/* EHCI operational register base address */
#define EHCI_BASE       0xe0000000

/* CSR register base for the LED output (LiteX default CSR base) */
#define LED_OUT_BASE    0xf0000000

/* ── EHCI capability register offsets (bytes from EHCI_BASE) ─────────────── */

#define EHCI_CAPLENGTH          0x00   /* Capability registers length (0x20) */
#define EHCI_HCIVERSION         0x02   /* EHCI revision (0x0100) */
#define EHCI_HCSPARAMS          0x04   /* Structural parameters */
#define EHCI_HCCPARAMS          0x08   /* Capability parameters */

/* ── EHCI operational register offsets (bytes from EHCI_BASE) ────────────── */
/* The operational register block starts at CAPLENGTH (0x20). */

#define EHCI_OP_BASE            0x20
#define EHCI_USBCMD             (EHCI_OP_BASE + 0x00)  /* USB Command */
#define EHCI_USBSTS             (EHCI_OP_BASE + 0x04)  /* USB Status */
#define EHCI_USBINTR            (EHCI_OP_BASE + 0x08)  /* USB Interrupt Enable */
#define EHCI_FRINDEX            (EHCI_OP_BASE + 0x0C)  /* Frame Index */
#define EHCI_CTRLDSSEGMENT      (EHCI_OP_BASE + 0x10)  /* Control Data Structure Segment */
#define EHCI_PERIODICLISTBASE   (EHCI_OP_BASE + 0x14)  /* Periodic Frame List Base */
#define EHCI_ASYNCLISTADDR      (EHCI_OP_BASE + 0x18)  /* Asynchronous List Address */
/* Reserved 0x3C - 0x5C */
#define EHCI_CONFIGFLAG         (EHCI_OP_BASE + 0x40)  /* Configure Flag */
/* Port Status & Control — one per port; PORTSC1 at OP_BASE + 0x44 */
#define EHCI_PORTSC_BASE        (EHCI_OP_BASE + 0x44)
#define EHCI_PORTSC_STRIDE      0x04
#define EHCI_PORTSC1            (EHCI_OP_BASE + 0x44)  /* Port Status & Control (port 1) */

/* ── USBCMD bits ──────────────────────────────────────────────────────────── */

#define USBCMD_RUN              0x00000001
#define USBCMD_HCRESET          0x00000002
#define USBCMD_FLS_MASK         0x0000000C  /* Frame List Size (1024/512/256) */
#define USBCMD_FLS_1024         0x00000000
#define USBCMD_FLS_512          0x00000004
#define USBCMD_FLS_256          0x00000008
#define USBCMD_PSE              0x00000010  /* Periodic Schedule Enable */
#define USBCMD_ASE              0x00000020  /* Async Schedule Enable */
#define USBCMD_IAA              0x00000040  /* Interrupt on Async Advance */
#define USBCMD_ASPME            0x00000800  /* Async Schedule Park Mode Enable */
#define USBCMD_ITC_MASK         0x00FF0000  /* Interrupt Threshold Control */

/* ── USBSTS bits ──────────────────────────────────────────────────────────── */

#define USBSTS_USBINT           0x00000001
#define USBSTS_ERROR            0x00000002
#define USBSTS_PCD              0x00000004  /* Port Change Detect */
#define USBSTS_FLR              0x00000008  /* Frame List Rollover */
#define USBSTS_HSE              0x00000010  /* Host System Error */
#define USBSTS_IAA              0x00000020  /* Interrupt on Async Advance */
#define USBSTS_HCH              0x00001000  /* HC Halted */
#define USBSTS_RECLAMATION      0x00002000
#define USBSTS_PSSTATUS         0x00004000  /* Periodic Schedule Status */
#define USBSTS_ASSTATUS         0x00008000  /* Async Schedule Status */

/* ── USBINTR bits ─────────────────────────────────────────────────────────── */

#define USBINTR_USBINT          0x00000001
#define USBINTR_ERRINT          0x00000002
#define USBINTR_PCD             0x00000004
#define USBINTR_FLR             0x00000008
#define USBINTR_HSE             0x00000010
#define USBINTR_IAA             0x00000020

/* ── PORTSC bits ──────────────────────────────────────────────────────────── */

#define PORTSC_CCS              0x00000001  /* Current Connect Status */
#define PORTSC_CSC              0x00000002  /* Connect Status Change */
#define PORTSC_PE               0x00000004  /* Port Enable */
#define PORTSC_PEC              0x00000008  /* Port Enable Change */
#define PORTSC_OCA              0x00000010  /* Over-Current Active */
#define PORTSC_OCC              0x00000020  /* Over-Current Change */
#define PORTSC_FPR              0x00000040  /* Force Port Resume */
#define PORTSC_SUSP             0x00000080  /* Suspend */
#define PORTSC_PR               0x00000100  /* Port Reset */
/* bit 9 is reserved in EHCI (there is no "high-speed port" bit) */
#define PORTSC_LINE_STATUS_MASK 0x00000C00
#define PORTSC_LINE_STATUS_D0   0x00000000
#define PORTSC_LINE_STATUS_K    0x00000400
#define PORTSC_LINE_STATUS_J    0x00000800
#define PORTSC_LINE_STATUS_SE0  0x00000C00
#define PORTSC_PP               0x00001000  /* Port Power */
#define PORTSC_PO               0x00002000  /* Port Owner (1=companion owns, 0=EHCI) */
#define PORTSC_PIC_MASK         0x0000C000  /* Port Indicator Control */
#define PORTSC_PTC_MASK         0x000F0000  /* Port Test Control */
#define PORTSC_WKCNNT_E         0x00100000  /* Wake on Connect Enable */
#define PORTSC_WKDSCNNT_E       0x00200000  /* Wake on Disconnect Enable */
#define PORTSC_WKOC_E           0x00400000  /* Wake on Over-current Enable */

/* ── Queue Head (QH) — 48 bytes / 12 DWORDs ───────────────────────────────── */

/*
 * QH layout (matching QueueHeadLayout):
 *   DWORD 0  HLP       Horizontal Link Pointer
 *   DWORD 1  EP_CHAR   Endpoint Characteristics
 *   DWORD 2  EP_CAP    Endpoint Capabilities
 *   DWORD 3  CUR_QTD   Current qTD Pointer (non-overlay)
 *   DWORD 4-11 Overlay area (qTD fields written by the HC)
 */
typedef struct {
    uint32_t hlp;        /* 0x00 Horizontal Link Pointer */
    uint32_t ep_char;    /* 0x04 Endpoint Characteristics */
    uint32_t ep_cap;     /* 0x08 Endpoint Capabilities */
    uint32_t cur_qtd;    /* 0x0C Current qTD Pointer */
    uint32_t overlay[8]; /* 0x10-0x2C Overlay */
} ehci_qh_t;

/* QH field masks / shared link-list constants */
#define QH_HLP_T            0x00000001
#define QH_HLP_TYP          0x00000002
#define QH_HLP_MASK         0xFFFFFFE0
#define QH_EP_DEV_MASK      0x0000007F
#define QH_SPEED_FULL       0
#define QH_SPEED_LOW        1
#define QH_SPEED_HIGH       2
#define QH_LINK_TERMINATE   QH_HLP_T

/* ── Queue Element Transfer Descriptor (qTD) — 32 bytes / 8 DWORDs ────────── */

/*
 * qTD layout (matching QueueTD):
 *   DWORD 0  NEXT_QTD   Next qTD Pointer
 *   DWORD 1  ALT_QTD    Alternate Next qTD Pointer
 *   DWORD 2  TOKEN      qTD Token
 *   DWORD 3-7  BUF0..BUF4 Buffer Page Pointers (5 x 4KB pages)
 */
typedef struct {
    uint32_t next_qtd;   /* 0x00 Next qTD Pointer */
    uint32_t alt_qtd;    /* 0x04 Alternate Next qTD Pointer */
    uint32_t token;      /* 0x08 qTD Token */
    uint32_t buf[5];     /* 0x0C-0x1C Buffer Page Pointers */
} ehci_qtd_t;

/* qTD token field bits */
#define QTD_TOKEN_STATUS_MASK  0x000000FF
#define QTD_TOKEN_ACTIVE       0x00000080
#define QTD_TOKEN_HALTED       0x00000040
#define QTD_TOKEN_DATABUFFER   0x00000020
#define QTD_TOKEN_BABBLE       0x00000010
#define QTD_TOKEN_XACTERR      0x00000008
#define QTD_TOKEN_MISSED       0x00000004
#define QTD_TOKEN_SPLITXSTATE  0x00000002
#define QTD_TOKEN_PINGSTATE    0x00000001
#define QTD_TOKEN_ERR_MASK     0x00000C00

#define QTD_TOKEN_PID_MASK     0x00000300
#define QTD_TOKEN_PID_OUT      0x00000000
#define QTD_TOKEN_PID_IN       0x00000100
#define QTD_TOKEN_PID_SETUP    0x00000200

#define QTD_TOKEN_CERR_MASK    0x00000C00
#define QTD_TOKEN_C_PAGE_MASK  0x00007000
#define QTD_TOKEN_IOC          0x00008000
#define QTD_TOKEN_BYTES_SHIFT  16
#define QTD_TOKEN_BYTES_MASK   0x7FFF0000

#define QTD_NEXT_QTD_TERMINATE 0x00000001

/* ── Frame List — 1024 entries × 4 bytes, 4096-byte aligned ───────────────── */

typedef struct {
    uint32_t link[1024];
} ehci_frame_list_t;

#define EHCI_FRAME_LIST_ENTRIES   1024
#define EHCI_FRAME_LIST_SIZE      4096
#define EHCI_FRAME_LIST_ENTRY_SIZE 4
#define EHCI_FRAME_LIST_ALIGNMENT 4096
#define EHCI_FL_T_BIT             0x00000001
#define EHCI_FL_LINK_MASK         0xFFFFFFE0

/* ── USB standard constants ───────────────────────────────────────────────── */

/* PID tokens (physical token packet PID codes) */
#define USB_PID_IN           0x69
#define USB_PID_OUT          0xE1
#define USB_PID_SETUP        0x2D

/* Descriptor types */
#define USB_DESC_DEVICE      1
#define USB_DESC_CONFIG      2
#define USB_DESC_HID         33   /* 0x21 */

/* HID class codes */
#define USB_CLASS_HID        3
#define USB_SUBCLASS_BOOT    1
#define USB_PROTOCOL_KEYBOARD 1
#define USB_PROTOCOL_MOUSE   2

/* HID requests */
#define USB_HID_REQ_BOOT     0x0B

/* Standard request codes */
#define USB_REQ_SET_ADDRESS       5
#define USB_REQ_GET_DESCRIPTOR    6
#define USB_REQ_SET_CONFIGURATION 9

#endif /* EHCI_H */
