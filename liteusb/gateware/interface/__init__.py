#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" PHY interface components. """

from .ulpi import (
    ULPIInterface,
    ULPIRegisterWindow,
    ULPIRxEventDecoder,
    ULPIControlTranslator,
    ULPITransmitTranslator,
    UTMITranslator,
)
from .utmi import (
    UTMIOperatingMode,
    UTMITerminationSelect,
    UTMITransmitInterface,
    UTMIInterfaceMultiplexer,
    UTMIInterface,
)

__all__ = [
    # ULPI Components
    "ULPIInterface",
    "ULPIRegisterWindow",
    "ULPIRxEventDecoder",
    "ULPIControlTranslator",
    "ULPITransmitTranslator",
    "UTMITranslator",
    # UTMI Components
    "UTMIOperatingMode",
    "UTMITerminationSelect",
    "UTMITransmitInterface",
    "UTMIInterfaceMultiplexer",
    "UTMIInterface",
]
