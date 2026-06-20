#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
""" Standard, full-gateware control request handlers. """

import os
import operator
import functools
from typing import Iterable, Callable
from warnings import warn

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue
from migen.genlib.misc import Case
from usb_protocol.types     import USBStandardFeatures, USBStandardRequests, USBRequestRecipient, USBRequestType
from usb_protocol.emitters  import DeviceDescriptorCollection

from ..usb2.request         import RequestHandlerInterface, USBRequestHandler
from ..usb2.descriptor      import GetDescriptorHandlerDistributed, GetDescriptorHandlerBlock, GetDescriptorHandlerMux
from ..stream               import USBInStreamInterface
from ...stream.generator    import StreamSerializer
from .                      import SetupPacket
from .control               import ControlRequestHandler


class StandardRequestHandler(ControlRequestHandler):
    """ Pure-gateware USB setup request handler. Implements the standard requests required for enumeration.

    Parameters
    ----------
    descriptors: DeviceDescriptorCollection
        The DeviceDescriptorCollection that contains our descriptors.
    max_packet_size: int, optional
        The maximum packet size for the endpoint associated with this handler.
    blacklist: deprecated, use skiplist instead
    skiplist:  iterable of functions that accept a SetupPacket and return a boolean
        Collection of functions that determine if a given packet will be handled by this request handler.
    avoid_blockram: int, optional
        If True, placing data into block RAM will be avoided.

     """

    def __init__(self, descriptors: DeviceDescriptorCollection, max_packet_size=64, avoid_blockram=None, blacklist: Iterable[Callable[[SetupPacket], "_Value"]] = (), skiplist: Iterable[Callable[[SetupPacket], "_Value"]] = ()):
        self.descriptors      = descriptors
        self._max_packet_size = max_packet_size
        self._avoid_blockram  = avoid_blockram
        if len(blacklist) > 0:
            warn("Argument 'blacklist' is deprecated; prefer 'skiplist'.", DeprecationWarning)
            if len(skiplist) > 0:
                warn("Only one of 'blacklist' or 'skiplist' should be specified.")
            self._skiplist = blacklist
        else:
            self._skiplist = skiplist

        # If we don't have a value for avoiding blockrams; defer to the environment.
        if self._avoid_blockram is None:
            self._avoid_blockram = os.getenv("LUNA_AVOID_BLOCKRAM", False)

        super().__init__()


    def get_descriptor_handler_submodule(self):

        # The distributed handler supports a combination of fixed and runtime descriptors directly...
        if self._avoid_blockram:
            return GetDescriptorHandlerDistributed(self.descriptors, max_packet_length=self._max_packet_size)

        # ...but the block handler does not. In this case, first we split the descriptors into two
        # collections: fixed descriptors (for the ROM) and runtime descriptors.
        fixed_descriptors       = DeviceDescriptorCollection()
        runtime_descriptors     = DeviceDescriptorCollection()
        has_runtime_descriptors = False
        for type_number, index, descriptor in self.descriptors:
            if isinstance(descriptor, bytes):
                fixed_descriptors.add_descriptor(descriptor, index=index, descriptor_type=type_number)
            else:
                runtime_descriptors.add_descriptor(descriptor, index=index, descriptor_type=type_number)
                has_runtime_descriptors = True

        # If there are runtime descriptors, we add a get descriptor multiplexer and a distributed handler.
        if has_runtime_descriptors:
            handler_mux = GetDescriptorHandlerMux()
            handler_mux.add_descriptor_handler(GetDescriptorHandlerBlock(fixed_descriptors, max_packet_length=self._max_packet_size))
            handler_mux.add_descriptor_handler(GetDescriptorHandlerDistributed(runtime_descriptors, max_packet_length=self._max_packet_size))
            return handler_mux
        else:
            return GetDescriptorHandlerBlock(self.descriptors, max_packet_length=self._max_packet_size)


    def elaborate(self, platform):
        interface = self.interface

        # Create convenience aliases for our interface components.
        setup               = interface.setup
        handshake_generator = interface.handshakes_out
        tx                  = interface.tx


        #
        # Submodules
        #
        # Handler for Get Descriptor requests; responds with our various fixed descriptors.
        self.submodules.get_descriptor = get_descriptor_handler = self.get_descriptor_handler_submodule()
        self.comb += [
            get_descriptor_handler.value.eq(setup.value),
            get_descriptor_handler.length.eq(setup.length),
        ]

        # Handler for various small-constant-response requests (GET_CONFIGURATION, GET_STATUS).
        self.submodules.transmitter = transmitter = \
            StreamSerializer(data_length=2, domain="usb", stream_type=USBInStreamInterface, max_length_width=2)


        #
        # Handlers.
        #
        # Only handle STANDARD request types
        skiplisted = functools.reduce(operator.__or__, (f(setup) for f in self._skiplist), C(0))
        self.comb += interface.claim.eq((setup.type == USBRequestType.STANDARD) & ~skiplisted)

        # FSM for handling standard requests
        fsm = FSM(reset_state='IDLE')
        self.submodules += fsm

        # IDLE -- not handling any active request
        fsm.act('IDLE',
            # Start at the beginning of our next / fresh GET_DESCRIPTOR request.
            NextValue(get_descriptor_handler.start_position, 0),
            # Always start our responses with DATA1 pids, per [USB 2.0: 8.5.3].
            NextValue(self.interface.tx_data_pid, 1),
            # If we've received a new setup packet, handle it.
            If(setup.received & (setup.type == USBRequestType.STANDARD),
                If(~skiplisted,
                    # Select which standard packet we're going to handle.
                    Case(setup.request, {
                        USBStandardRequests.GET_STATUS:        NextState('GET_STATUS'),
                        USBStandardRequests.CLEAR_FEATURE:     NextState('CLEAR_FEATURE'),
                        USBStandardRequests.SET_ADDRESS:       NextState('SET_ADDRESS'),
                        USBStandardRequests.SET_CONFIGURATION: NextState('SET_CONFIGURATION'),
                        USBStandardRequests.GET_DESCRIPTOR:    NextState('GET_DESCRIPTOR'),
                        USBStandardRequests.GET_CONFIGURATION: NextState('GET_CONFIGURATION'),
                        'default':                             NextState('UNHANDLED'),
                    })
                )
            )
        )

        # GET_STATUS -- Fetch the device's status.
        # For now, we'll always return '0'.
        fsm.act('GET_STATUS',
            # TODO: handle reporting endpoint stall status
            # TODO: copy the remote wakeup and bus-powered attributes from bmAttributes of the relevant descriptor?
            self.handle_simple_data_request(fsm, transmitter, 0, length=2)
        )

        # CLEAR_FEATURE
        stall_condition = Signal()
        fsm.act('CLEAR_FEATURE',
            # Define stall condition
            stall_condition.eq((setup.recipient != USBRequestRecipient.ENDPOINT) |
                               (setup.value != USBStandardFeatures.ENDPOINT_HALT)),
            # Provide a response to the STATUS stage.
            If(interface.status_requested,
                # If our stall condition is met, stall; otherwise, send a ZLP [USB 8.5.3].
                # For now, we only implement clearing ENDPOINT_HALT.
                If(stall_condition,
                    handshake_generator.stall.eq(1),
                ).Else(
                    self.send_zlp(),
                )
            ),
            # Accept the relevant value after the packet is ACK'd...
            If(interface.handshakes_in.ack,
                interface.clear_endpoint_halt.enable.eq(1),
                interface.clear_endpoint_halt.direction.eq(setup.index[7]),
                interface.clear_endpoint_halt.number.eq(setup.index[0:4]),
                # ... and then return to idle.
                NextState('IDLE'),
            )
        )

        # SET_ADDRESS -- The host is trying to assign us an address.
        fsm.act('SET_ADDRESS',
            self.handle_register_write_request(fsm, interface.new_address, interface.address_changed)
        )

        # SET_CONFIGURATION -- The host is trying to select an active configuration.
        fsm.act('SET_CONFIGURATION',
            # TODO: stall if we don't have a relevant configuration
            self.handle_register_write_request(fsm, interface.new_config, interface.config_changed)
        )

        # GET_DESCRIPTOR -- The host is asking for a USB descriptor -- for us to "self describe".
        expecting_ack = Signal()

        fsm.act('GET_DESCRIPTOR',
            get_descriptor_handler.tx.connect(tx),
            handshake_generator.stall.eq(get_descriptor_handler.stall),

            # Respond to our data stage with a descriptor...
            If(interface.data_requested,
                get_descriptor_handler.start.eq(1),
                NextValue(expecting_ack, 1),
            ),

            # Each time we receive an ACK, advance in our descriptor.
            # This allows us to send descriptors with >64B of content.
            If(interface.handshakes_in.ack & expecting_ack,
                # NOTE: this logic might need to be scaled by bytes-per-word for USB3, if it's ever used.
                # For now, we're not using it on USB3 at all, since we assume descriptors always fit in a
                # USB3 packet.
                NextValue(get_descriptor_handler.start_position, 
                          get_descriptor_handler.start_position + self._max_packet_size),
                NextValue(self.interface.tx_data_pid, ~self.interface.tx_data_pid),
                NextValue(expecting_ack, 0),
            ),

            # ... and ACK our status stage.
            If(interface.status_requested,
                handshake_generator.ack.eq(1),
                NextState('IDLE'),
            ),

            # If the requested descriptor doesn't exist, the request is terminated by STALLing the data stage.
            If(get_descriptor_handler.stall,
                NextValue(expecting_ack, 0),
                NextState('IDLE'),
            )
        )

        # GET_CONFIGURATION -- The host is asking for the active configuration number.
        fsm.act('GET_CONFIGURATION',
            self.handle_simple_data_request(fsm, transmitter, interface.active_config)
        )

        # UNHANDLED -- we've received a request we're not prepared to handle
        fsm.act('UNHANDLED',
            # When we next have an opportunity to stall, do so,
            # and then return to idle.
            If(interface.data_requested | interface.status_requested,
                handshake_generator.stall.eq(1),
                NextState('IDLE'),
            )
        )
