#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" EHCI data structure definitions — memory layouts matching the EHCI specification.

References
----------
Enhanced Host Controller Interface Specification for Universal Serial Bus, Rev 1.0
Section 3: Data Structures

All structures below describe the 32-bit little-endian DWORD layouts
used by the EHCI host controller to traverse the periodic and asynchronous
schedules, and to execute USB transfers.
"""

from migen import *


# ── Queue Head (QH) — 48 bytes / 12 DWORDs ──────────────────────────────────

class QueueHeadLayout:
    """ Memory layout of an EHCI Queue Head (48 bytes).

    Used for interrupt, control, and bulk endpoints.

    DWORD 0: Horizontal Link Pointer (HLP)
        [31:5]  QHLP      — pointer to next QH/iTD/siTD/FSTN (DWORD-aligned)
        [4:3]   Reserved
        [2]     Typ       — 0 = iTD, 1 = QH (for periodic), 
                             0 = QH, 1 = FSTN (for async)
        [1]     T         — Terminate (1 = end of list)
        [0]     Reserved

    DWORD 1: Endpoint Characteristics
        [31:28] RL        — NAK Count Reload (interrupt only)
        [27:16] C         — Control (interrupt schedule mask)
        [15]    H         — High-bandwidth pipe selector
        [14:12] EPS       — Endpoint Speed (0=FS, 1=LS, 2=HS)
        [11:8]  EP        — Endpoint number
        [7]     I         — Inactivate on Next Transaction
        [6:0]   DEV       — Device Address

    DWORD 2: Endpoint Capabilities
        [31:30] Mult      — High-bandwidth multiplier (1/2/3 transactions per μframe)
        [29:23] PortNum   — Port number (for split transactions)
        [22:16] HubAddr   — Hub address (0 if not split)
        [15:8]  C-mask    — Split completion mask
        [7:0]   S-mask    — Split start mask

    DWORD 3: Current qTD Pointer (non-overlay)
        [31:5]  Pointer   — DWORD-aligned pointer to current qTD
        [4:0]   Reserved

    DWORD 4-11: Overlay area (qTD fields written by the HC)
    """
    SIZE_BYTES = 48
    SIZE_DWORDS = 12

    # DWORD offsets
    OFF_HLP          = 0   # Horizontal Link Pointer
    OFF_EP_CHAR      = 1   # Endpoint Characteristics
    OFF_EP_CAP       = 2   # Endpoint Capabilities
    OFF_CUR_QTD      = 3   # Current qTD Pointer
    OFF_OVERLAY_BASE = 4   # Overlay area starts here
    OFF_NEXT_QTD     = 4   # Overlay: Next qTD Pointer
    OFF_ALT_QTD      = 5   # Overlay: Alternate Next qTD Pointer
    OFF_TOKEN        = 6   # Overlay: qTD Token
    OFF_BUF0         = 7   # Overlay: Buffer Page 0
    OFF_BUF1         = 8   # Overlay: Buffer Page 1
    OFF_BUF2         = 9   # Overlay: Buffer Page 2
    OFF_BUF3         = 10  # Overlay: Buffer Page 3
    OFF_BUF4         = 11  # Overlay: Buffer Page 4

    # HLP field bits
    HLP_T              = 1
    HLP_TYP            = 2
    HLP_MASK           = 0xFFFFFFE0

    # EP_CHAR field masks
    EP_DEV_MASK        = 0x0000007F

    # Endpoint speed
    SPEED_FULL         = 0
    SPEED_LOW          = 1
    SPEED_HIGH         = 2


# ── Queue Element Transfer Descriptor (qTD) — 32 bytes / 8 DWORDs ──────────

class QueueTD:
    """ Memory layout of an EHCI Queue Element Transfer Descriptor (32 bytes).

    Describes a single bulk/control/interrupt transfer.

    DWORD 0: Next qTD Pointer
        [31:5]  Pointer
        [4:1]   Reserved
        [0]     T         — Terminate

    DWORD 1: Alternate Next qTD Pointer (only used for short packet handling)
        [31:5]  Pointer
        [4:1]   Reserved
        [0]     T         — Terminate

    DWORD 2: qTD Token
        [31]    Data Toggle on error override
        [30:16] Total Bytes to Transfer (15-bit; 0 = 0 bytes, 0x7FFF = zero-length)
        [15]    IOC        — Interrupt On Complete
        [14:12] C_Page     — Current Page (0-4)
        [11:10] CERR       — Error Counter (3 = max retries)
        [9:8]   PID        — 0=OUT, 1=IN, 2=SETUP
        [7:0]   Status     — Active | Halted | Data Buffer Error | Babble | XactErr | Missed μFrame | SplitXState | PingState

    DWORD 3-7: Buffer Page Pointers (5 × 4KB pages = up to 20KB)
        [31:12] Page address
        [11:0]  Current Offset (during execution)
    """
    SIZE_BYTES = 32
    SIZE_DWORDS = 8

    OFF_NEXT_QTD    = 0
    OFF_ALT_QTD     = 1
    OFF_TOKEN       = 2
    OFF_BUF0        = 3
    OFF_BUF1        = 4
    OFF_BUF2        = 5
    OFF_BUF3        = 6
    OFF_BUF4        = 7

    # Token field bits
    TOKEN_STATUS_MASK          = 0x000000FF
    TOKEN_ACTIVE               = 0x80
    TOKEN_HALTED               = 0x40
    TOKEN_DATABUFFER           = 0x20
    TOKEN_BABBLE               = 0x10
    TOKEN_XACTERR              = 0x08
    TOKEN_MISSED               = 0x04
    TOKEN_SPLITXSTATE          = 0x02
    TOKEN_PINGSTATE            = 0x01
    TOKEN_ERR_MASK             = 0x7E   # All error bits except PingState

    TOKEN_PID                  = 0x00000300
    TOKEN_PID_OUT              = 0x00000000
    TOKEN_PID_IN               = 0x00000100
    TOKEN_PID_SETUP            = 0x00000200

    TOKEN_CERR                 = 0x00000C00
    TOKEN_C_PAGE               = 0x00007000
    TOKEN_IOC                  = 0x00008000
    TOKEN_BYTES_SHIFT          = 16

    TOKEN_NEXT_QTD_TERMINATE   = 0x00000001


# ── Isochronous Transfer Descriptor (iTD) — 64 bytes / 16 DWORDs ───────────

class IsochronousTD:
    """ Memory layout of an EHCI iTD (64 bytes).

    Describes high-speed isochronous transfers for up to 8 microframes.

    DWORD 0: Next Link Pointer
        [31:5]  Pointer
        [4:1]   Reserved
        [0]     T         — Terminate

    DWORD 1-8: Transaction descriptors [0]..[7]
        Each DWORD:
        [31]    IOC        — Interrupt On Complete (for this transaction)
        [30:16] Length     — Bytes to transfer this μframe
        [15]    PG         — Page select (0-6)
        [14:12] Offset     — Offset within page
        [11:0]  Status     — Active | Babble | XactErr | Data Buffer Error
        Status bits: [11]=Active [10]=Babble [9]=XactErr [8]=DataBufErr [7..0]=actual length

    DWORD 9-15: Buffer Page Pointers (7 pages × 4KB = 28KB)
        [31:12] Page address
        [11:0]  Must be zero
    """
    SIZE_BYTES = 64
    SIZE_DWORDS = 16

    OFF_NEXT_LINK     = 0
    OFF_TRANSACTION0  = 1
    OFF_TRANSACTION1  = 2
    OFF_TRANSACTION2  = 3
    OFF_TRANSACTION3  = 4
    OFF_TRANSACTION4  = 5
    OFF_TRANSACTION5  = 6
    OFF_TRANSACTION6  = 7
    OFF_TRANSACTION7  = 8
    OFF_BUF0          = 9
    OFF_BUF1          = 10
    OFF_BUF2          = 11
    OFF_BUF3          = 12
    OFF_BUF4          = 13
    OFF_BUF5          = 14
    OFF_BUF6          = 15

    # Transaction status bits
    TRANS_ACTIVE      = 0x800


# ── Split Transaction Isochronous TD (siTD) — 32 bytes / 8 DWORDs ──────────

class SplitIsochronousTD:
    """ Memory layout of an EHCI siTD (32 bytes).

    Used for full-/low-speed isochronous transfers through transaction translators.

    DWORD 0: Next Link Pointer
        [31:5] Pointer
        [4:1]  Reserved
        [0]    T

    DWORD 1: Endpoint characteristics
        [31]    Direction (0=OUT, 1=IN)
        [30:24] Port Number
        [23:16] Hub Address
        [15:12] Reserved
        [11:8]  Endpoint
        [7:0]   Device Address

    DWORD 2: μFrame S-mask and C-mask
        [15:8]  C-mask
        [7:0]   S-mask

    DWORD 3: Status/Transfer descriptor
        [31]    Active
        [30]    IOC
        [29]    PG (page select)
        [28:16] Total bytes (0 = zero-length)
        [15:8]  μFrame (completion μframe number)
        [7:0]   Status (Babble | XactErr | Missed | SplitXState | Error)

    DWORD 4-5: Buffer Page pointers (2 pages)
    DWORD 6: Back Link Pointer
    DWORD 7: Buffer Pointer (for SPLIT start/complete buffer differentiation)
    """
    SIZE_BYTES = 32
    SIZE_DWORDS = 8

    OFF_NEXT_LINK     = 0
    OFF_EP_CHAR       = 1
    OFF_MASKS         = 2
    OFF_STATUS        = 3
    OFF_BUF0          = 4
    OFF_BUF1          = 5
    OFF_BACK_LINK     = 6
    OFF_BUF_START     = 7

    STATUS_ACTIVE     = 0x80000000


# ── Frame List ──────────────────────────────────────────────────────────────

class FrameList:
    """ EHCI Periodic Frame List.

    1024 × 32-bit entries, 4096-byte aligned.
    Each entry points to an iTD, siTD, or QH (periodic schedule) or is null (T=1).

    The host controller indexes into this list using bits [12:3] of FRINDEX.
    """
    SIZE_BYTES = 4096
    NUM_ENTRIES = 1024
    ENTRY_SIZE = 4  # bytes
    ALIGNMENT = 4096

    T_BIT = 0x00000001
    LINK_MASK = 0xFFFFFFE0


# ── EHCI Operational Register Offsets ───────────────────────────────────────

class EHCIRegisters:
    """ EHCI operational register map offsets (in 32-bit DWORDs).

    Registers:
        USBCMD           — USB Command
        USBSTS           — USB Status
        USBINTR          — USB Interrupt Enable
        FRINDEX          — Frame Index
        CTRLDSSEGMENT    — Control Data Structure Segment (64-bit support)
        PERIODICLISTBASE — Periodic Frame List Base Address
        ASYNCLISTADDR    — Asynchronous List Address
        CONFIGFLAG       — Configure Flag
        PORTSC1          — Port Status & Control (port 1)
    """

    # Operational register offsets (in bytes from operational base)
    OFF_USBCMD              = 0x00
    OFF_USBSTS              = 0x04
    OFF_USBINTR             = 0x08
    OFF_FRINDEX             = 0x0C
    OFF_CTRLDSSEGMENT       = 0x10
    OFF_PERIODICLISTBASE    = 0x14
    OFF_ASYNCLISTADDR       = 0x18
    # Reserved 0x1C - 0x3C
    OFF_CONFIGFLAG          = 0x40
    # Port Status & Control — one per port; PORTSC1 at 0x44
    OFF_PORTSC_BASE         = 0x44
    OFF_PORTSC_STRIDE       = 0x04

    # USBCMD bits
    USBCMD_RUN              = 0x00000001
    USBCMD_HCRESET          = 0x00000002
    USBCMD_FLS_MASK         = 0x0000000C  # Frame List Size (1024/512/256)
    USBCMD_FLS_1024         = 0x00000000
    USBCMD_FLS_512          = 0x00000004
    USBCMD_FLS_256          = 0x00000008
    USBCMD_PSE              = 0x00000010  # Periodic Schedule Enable
    USBCMD_ASE              = 0x00000020  # Async Schedule Enable
    USBCMD_IAA              = 0x00000040  # Interrupt on Async Advance
    USBCMD_ASPME            = 0x00000800  # Async Schedule Park Mode Enable
    USBCMD_ITC_MASK         = 0x00FF0000  # Interrupt Threshold Control

    # USBSTS bits
    USBSTS_USBINT           = 0x00000001
    USBSTS_ERROR            = 0x00000002
    USBSTS_PCD              = 0x00000004  # Port Change Detect
    USBSTS_FLR              = 0x00000008  # Frame List Rollover
    USBSTS_HSE              = 0x00000010  # Host System Error
    USBSTS_IAA              = 0x00000020  # Interrupt on Async Advance
    USBSTS_HCH              = 0x00001000  # HC Halted
    USBSTS_RECLAMATION      = 0x00002000
    USBSTS_PSSTATUS         = 0x00004000  # Periodic Schedule Status
    USBSTS_ASSTATUS         = 0x00008000  # Async Schedule Status

    # USBINTR bits
    USBINTR_USBINT          = 0x00000001
    USBINTR_ERRINT          = 0x00000002
    USBINTR_PCD             = 0x00000004
    USBINTR_FLR             = 0x00000008
    USBINTR_HSE             = 0x00000010
    USBINTR_IAA             = 0x00000020

    # PORTSC bits
    PORTSC_CCS              = 0x00000001  # Current Connect Status
    PORTSC_CSC              = 0x00000002  # Connect Status Change
    PORTSC_PE               = 0x00000004  # Port Enable
    PORTSC_PEC              = 0x00000008  # Port Enable Change
    PORTSC_OCA              = 0x00000010  # Over-Current Active
    PORTSC_OCC              = 0x00000020  # Over-Current Change
    PORTSC_FPR              = 0x00000040  # Force Port Resume
    PORTSC_SUSP             = 0x00000080  # Suspend
    PORTSC_PR               = 0x00000100  # Port Reset
    PORTSC_HSP              = 0x00000200  # High-Speed Port
    PORTSC_LINE_STATUS_MASK = 0x00000C00
    PORTSC_LINE_STATUS_D0   = 0x00000000
    PORTSC_LINE_STATUS_K    = 0x00000400
    PORTSC_LINE_STATUS_J    = 0x00000800
    PORTSC_LINE_STATUS_SE0  = 0x00000C00
    PORTSC_PP               = 0x00001000  # Port Power
    PORTSC_PO               = 0x00002000  # Port Owner (1=EHCI owns, 0=companion)
    PORTSC_PTC_MASK         = 0x000F0000  # Port Test Control
    PORTSC_PIC_MASK         = 0x00C00000  # Port Indicator Control
    PORTSC_WKOC_E           = 0x00400000  # Wake on Over-current Enable
    PORTSC_WKDSCNNT_E       = 0x00800000  # Wake on Disconnect Enable
    PORTSC_WKCNNT_E         = 0x01000000  # Wake on Connect Enable
