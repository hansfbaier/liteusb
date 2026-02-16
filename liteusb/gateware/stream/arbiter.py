#
# This file is part of LUNA / LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Stream arbiters. """

from migen import *
from migen.genlib.fsm import FSM, NextState

from . import StreamInterface


class StreamArbiter(Module):
    """ Convenience variant of our StreamArbiter that operates streams in a given domain. """

    def __init__(self, stream_type=StreamInterface, domain="sync"):
        self.stream_type = stream_type
        self.domain = domain
