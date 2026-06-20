#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
""" Request components shared between USB2 and USB3. """

from migen import *
from migen.genlib.record import Record


class SetupPacket(Record):
    """ Record capturing the content of a setup packet.

    Components (O = output from setup parser; read-only input to others):
        O: received      -- Strobe; indicates that a new setup packet has been received,
                            and thus this data has been updated.

        O: is_in_request -- High if the current request is an 'in' request.
        O: type[2]       -- Request type for the current request.
        O: recipient[5]  -- Recipient of the relevant request.

        O: request[8]    -- Request number.
        O: value[16]     -- Value argument for the setup request.
        O: index[16]     -- Index argument for the setup request.
        O: length[16]    -- Length of the relevant setup request.
    """

    def __init__(self):
        super().__init__([
            # Byte 1
            ('recipient',      5),
            ('type',           2),
            ('is_in_request',  1),

            # Byte 2
            ('request',        8),

            # Byte 3/4
            ('value',         16),

            # Byte 5/6
            ('index',         16),

            # Byte 7/8
            ('length',        16),

            # Control signaling.
            ('received',       1),
        ])
