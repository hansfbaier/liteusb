#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" LiteUSB - USB device gateware library. """

# ---------------------------------------------------------------------------
# Compatibility: migen's Record.connect() requires 3-tuple layouts
# (name, width, DIR_*), but liteusb Records use Amaranth-style 2-tuples.
# Monkey-patch Record.connect to handle 2-tuple fields gracefully.
# ---------------------------------------------------------------------------
from migen.genlib.record import Record, DIR_NONE, DIR_M_TO_S, DIR_S_TO_M
from migen.fhdl.structure import Signal
from functools import reduce
from operator import or_

_original_connect = Record.connect
def _patched_connect(self, *slaves, keep=None, omit=None):
    if keep is None:
        _keep = set([f[0] for f in self.layout])
    elif isinstance(keep, list):
        _keep = set(keep)
    else:
        _keep = keep
    if omit is None:
        _omit = set()
    elif isinstance(omit, list):
        _omit = set(omit)
    else:
        _omit = omit
    _keep = _keep - _omit

    r = []
    for f in self.layout:
        field = f[0]
        self_e = getattr(self, field)
        if isinstance(self_e, Signal):
            if field in _keep:
                direction = f[2] if len(f) >= 3 else DIR_NONE
                if direction == DIR_M_TO_S:
                    r += [getattr(slave, field).eq(self_e) for slave in slaves]
                elif direction == DIR_S_TO_M:
                    r.append(self_e.eq(reduce(or_, [getattr(slave, field) for slave in slaves])))
                elif direction == DIR_NONE:
                    r += [getattr(slave, field).eq(self_e) for slave in slaves]
                else:
                    raise TypeError
        else:
            for slave in slaves:
                r += self_e.connect(getattr(slave, field), keep=keep, omit=omit)
    return r

Record.connect = _patched_connect
# ---------------------------------------------------------------------------

from .gateware.usb.device import USBDevice
from .gateware.usb.stream import (
    USBInStreamInterface,
    USBOutStreamInterface,
    USBOutStreamBoundaryDetector,
    USBRawSuperSpeedStream,
    SuperSpeedStreamArbiter,
    SuperSpeedStreamInterface,
)
from .gateware.stream import StreamInterface
from .gateware.interface.ulpi import ULPIInterface, UTMITranslator
from .gateware.interface.utmi import UTMIInterface, UTMITransmitInterface

__all__ = [
    # Main USB Device
    "USBDevice",
    # Stream Interfaces
    "USBInStreamInterface",
    "USBOutStreamInterface",
    "USBOutStreamBoundaryDetector",
    "USBRawSuperSpeedStream",
    "SuperSpeedStreamArbiter",
    "SuperSpeedStreamInterface",
    "StreamInterface",
    # PHY Interfaces
    "ULPIInterface",
    "UTMITranslator",
    "UTMIInterface",
    "UTMITransmitInterface",
]
