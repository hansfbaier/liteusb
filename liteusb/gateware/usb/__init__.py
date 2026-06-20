#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" USB gateware components. """

from .device import USBDevice
from .stream import (
    USBInStreamInterface,
    USBOutStreamInterface,
    USBOutStreamBoundaryDetector,
    USBRawSuperSpeedStream,
    SuperSpeedStreamArbiter,
    SuperSpeedStreamInterface,
)
from .usb2 import USBSpeed, USBPIDCategory, USBDirection, USBPacketID
from .usb2.device import USBDevice as USB2Device
from .usb2.control import USBControlEndpoint
from .usb2.endpoint import EndpointInterface, USBEndpointMultiplexer
from .usb2.packet import (
    HandshakeExchangeInterface,
    DataCRCInterface,
    TokenDetectorInterface,
    InterpacketTimerInterface,
    USBTokenDetector,
    USBHandshakeDetector,
    USBDataPacketCRC,
    USBDataPacketReceiver,
    USBDataPacketGenerator,
    USBHandshakeGenerator,
    USBInterpacketTimer,
)
from .usb2.reset import USBResetSequencer
from .usb2.transfer import USBInTransferManager
from .usb2.descriptor import (
    USBDescriptorStreamGenerator,
    GetDescriptorHandlerDistributed,
    GetDescriptorHandlerBlock,
    GetDescriptorHandlerMux,
)
from .request import SetupPacket

__all__ = [
    # Device
    "USBDevice",
    "USB2Device",
    # Stream Interfaces
    "USBInStreamInterface",
    "USBOutStreamInterface",
    "USBOutStreamBoundaryDetector",
    "USBRawSuperSpeedStream",
    "SuperSpeedStreamArbiter",
    "SuperSpeedStreamInterface",
    # USB2 Constants
    "USBSpeed",
    "USBPIDCategory",
    "USBDirection",
    "USBPacketID",
    # USB2 Components
    "USBControlEndpoint",
    "EndpointInterface",
    "USBEndpointMultiplexer",
    "USBResetSequencer",
    "USBInTransferManager",
    # USB2 Packet Components
    "HandshakeExchangeInterface",
    "DataCRCInterface",
    "TokenDetectorInterface",
    "InterpacketTimerInterface",
    "USBTokenDetector",
    "USBHandshakeDetector",
    "USBDataPacketCRC",
    "USBDataPacketReceiver",
    "USBDataPacketGenerator",
    "USBHandshakeGenerator",
    "USBInterpacketTimer",
    # USB2 Descriptor Components
    "USBDescriptorStreamGenerator",
    "GetDescriptorHandlerDistributed",
    "GetDescriptorHandlerBlock",
    "GetDescriptorHandlerMux",
    # Request Components
    "SetupPacket",
]
