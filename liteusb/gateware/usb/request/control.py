#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
""" Full-gateware control request handlers. """

from migen import *
from migen.genlib.fsm import NextState

from ..usb2.request import USBRequestHandler


class ControlRequestHandler(USBRequestHandler):
    """ Pure-gateware USB control request handler. """

    def handle_register_write_request(self, fsm, new_value_signal, write_strobe, stall_condition=0):
        """ Fills in the current state with a request handler meant to set a register.

        Parameters:
            fsm              -- The FSM module we're working with.
            new_value_signal -- The signal to receive the new value to be applied to the relevant register.
            write_strobe     -- The signal which will be pulsed when new_value_signal contains a update.
            stall_condition  -- If provided, if this condition is true, the request will be STALL'd instead
                                of acknowledged.
        """
        interface = self.interface

        # Provide a response to the STATUS stage.
        If(interface.status_requested,
            # If our stall condition is met, stall; otherwise, send a ZLP [USB 8.5.3].
            If(stall_condition,
                interface.handshakes_out.stall.eq(1)
            ).Else(
                *self.send_zlp()
            )
        ),

        # Accept the relevant value after the packet is ACK'd...
        If(interface.handshakes_in.ack,
            write_strobe.eq(1),
            new_value_signal.eq(interface.setup.value),
            # ... and then return to idle.
            NextState('IDLE')
        )

    def handle_simple_data_request(self, fsm, transmitter, data, length=1):
        """ Fills in a given current state with a request that returns a given piece of data.

        For e.g. GET_CONFIGURATION and GET_STATUS requests.

        Parameters:
            fsm         -- The FSM module we're working with.
            transmitter -- The transmitter module we're working with.
            data        -- The data to be returned.
            length      -- The length of the data to be returned.
        """
        interface = self.interface

        # Connect our transmitter up to the output stream...
        transmitter.stream.attach(interface.tx),
        Cat(transmitter.data[0:length]).eq(data),
        transmitter.max_length.eq(length),

        # ... trigger it to respond when data's requested...
        If(interface.data_requested,
            transmitter.start.eq(1)
        ),

        # ... and ACK our status stage.
        If(interface.status_requested,
            interface.handshakes_out.ack.eq(1),
            NextState('IDLE')
        )
