#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" EHCI USB 2.0 Host Controller — liteusb-based host controller gateware.

Reuses the liteusb device-stack packet layer (CRC, handshake, data packet
transmission/reception, ULPI/UTMI PHY interfaces) and adds EHCI-compatible
host controller logic for USB 2.0 HS/FS/LS operation with integrated TT.
"""

from .ehci import USBHostController
from .token_generator import USBHostTokenGenerator, USBSOFCounter
from .transfer import USBHostTransferEngine, HostTransferRequest, HostTransferResponse
from .schedule import EHCIScheduleProcessor
from .registers import EHCIRegisterFile
from .reset_host import HostResetSequencer
from .transaction_translator import TransactionTranslator
from .data_structures import (
    QueueHeadLayout, QueueTD, IsochronousTD,
    SplitIsochronousTD, FrameList, EHCIRegisters,
)

__all__ = [
    # Top-level
    "USBHostController",
    # Token generation
    "USBHostTokenGenerator",
    "USBSOFCounter",
    # Transfer execution
    "USBHostTransferEngine",
    "HostTransferRequest",
    "HostTransferResponse",
    # Schedule processing
    "EHCIScheduleProcessor",
    # Register file
    "EHCIRegisterFile",
    # Speed detection
    "HostResetSequencer",
    # Transaction Translator
    "TransactionTranslator",
    # Data structures
    "QueueHeadLayout",
    "QueueTD",
    "IsochronousTD",
    "SplitIsochronousTD",
    "FrameList",
    "EHCIRegisters",
]
