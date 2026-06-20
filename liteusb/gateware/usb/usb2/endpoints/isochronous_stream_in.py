#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2025 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Endpoint interfaces for isochronous endpoints.

These interfaces provide interfaces for connecting streams or stream-like
interfaces to hosts via isochronous pipes.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState

from ..endpoint     import EndpointInterface
from ....stream     import StreamInterface


class USBIsochronousStreamInEndpoint(Module):
    """ Isochronous endpoint that presents a stream-like interface.

    Used for repeatedly streaming data to a host from a stream-like interface.
    Intended to be useful as a transport for e.g. video or audio data.

    Attributes
    ----------
    stream: StreamInterface, input stream
        Full-featured stream interface that carries the data we'll transmit to the host.

    interface: EndpointInterface
        Communications link to our USB core.

    data_requested: Signal(), output
        Strobes, when a new packet starts

    frame_finished: Signal(), output
        Strobes immediately after the last byte in a frame has been transmitted

    bytes_in_frame: Signal(range(0, 3073)), input
        Specifies how many bytes will be transferred during this frame. If this is 0,
        a single ZLP will be emitted; for any other value one, two, or three packets
        will be generated, depending on the packet size. Latched in at the start of
        each frame.

        The maximum allowed value for this signal depends on the number of transfers
        per (micro)frame:
        - If this is a high-speed, high-throughput endpoint (descriptor indicates
          maxPacketSize > 512 and multiple transfers per microframe), then this value
          maxes out at (N * maxPacketSize), where N is the number of transfers per microframe.
        - For all other configurations, this must be <= the maximum packet size.

    Parameters
    ----------
    endpoint_number: int
        The endpoint number (not address) this endpoint should respond to.
    max_packet_size: int
        The maximum packet size for this endpoint. Should match the wMaxPacketSize provided in the
        USB endpoint descriptor.
    """

    _MAX_FRAME_DATA = 1024 * 3

    def __init__(self, *, endpoint_number, max_packet_size):
        self._endpoint_number = endpoint_number
        self._max_packet_size = max_packet_size

        #
        # I/O Port
        #
        self.interface      = EndpointInterface()
        self.stream         = StreamInterface()
        self.data_requested = Signal()
        self.frame_finished = Signal()

        self.bytes_in_frame = Signal(max=self._MAX_FRAME_DATA + 1)

        #
        # Internal logic
        #

        # Shortcuts.
        interface        = self.interface
        tx_stream        = interface.tx
        new_frame        = interface.tokenizer.new_frame

        targeting_ep_num = (interface.tokenizer.endpoint == self._endpoint_number)
        targeting_us     = targeting_ep_num & interface.tokenizer.is_in
        data_requested   = targeting_us & interface.tokenizer.ready_for_response

        # Track our transmission state.
        bytes_left_in_frame  = Signal.like(self.bytes_in_frame)
        bytes_left_in_packet = Signal(max=self._max_packet_size + 1, reset=self._max_packet_size - 1)
        next_data_pid        = Signal(2)
        tx_cnt               = Signal(max=self._MAX_FRAME_DATA)
        next_byte            = Signal.like(tx_cnt)

        # Helper signals for SEND_DATA state
        last_byte_in_packet  = Signal()
        last_byte_in_frame   = Signal()
        byte_terminates_send = Signal()

        self.comb += [
            tx_stream.payload.eq(0),
            interface.tx_pid_toggle.eq(next_data_pid),

            # Helper signal computations
            last_byte_in_packet.eq(bytes_left_in_packet <= 1),
            last_byte_in_frame.eq(bytes_left_in_frame <= 1),
            byte_terminates_send.eq(last_byte_in_packet | last_byte_in_frame),
        ]

        # Reset our state at the start of each frame.
        self.sync.usb += [
            If(new_frame,
                # Latch in how many bytes we'll be transmitting this frame.
                bytes_left_in_frame.eq(self.bytes_in_frame),

                # And start with a full packet to transmit.
                bytes_left_in_packet.eq(self._max_packet_size),

                # If it'll take more than two packets to send our data, start off with DATA2.
                # We'll follow with DATA1 and DATA0.
                If(self.bytes_in_frame > (2 * self._max_packet_size),
                    next_data_pid.eq(2)
                # Otherwise, if we need two, start with DATA1.
                ).Elif(self.bytes_in_frame > self._max_packet_size,
                    next_data_pid.eq(1)
                # Otherwise, we'll start (and end) with DATA0.
                ).Else(
                    next_data_pid.eq(0)
                )
            )
        ]

        #
        # Core sequencing FSM.
        #
        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        # IDLE -- the host hasn't yet requested data from our endpoint.
        fsm.act("IDLE",
            # Once the host requests a packet from us...
            If(data_requested,
                # If we have data to send, send it.
                If(bytes_left_in_frame,
                    NextState("SEND_DATA")
                # Otherwise, we'll send a ZLP.
                ).Else(
                    NextState("SEND_ZLP")
                )
            )
        )

        # SEND_DATA -- our primary data-transmission state; handles packet transmission
        fsm.act("SEND_DATA",
            # Don't advance ... until our data is accepted.
            If(tx_stream.ready,
                # If we've just completed transmitting a packet, or we've
                # just transmitted a full frame, end our transmission.
                If(byte_terminates_send,
                    NextState("IDLE")
                )
            )
        )

        # SEND_ZLP -- sends a zero-length packet, and then return to idle.
        fsm.act("SEND_ZLP",
            NextState("IDLE")
        )

        # Synchronous output assignments to match Amaranth timing (m.d.usb +=)
        self.sync.usb += [
            # Default frame_finished
            self.frame_finished.eq(0),

            # IDLE state outputs
            If(fsm.ongoing("IDLE"),
                tx_cnt.eq(0),
                tx_stream.first.eq(0),

                # Once the host requests a packet from us...
                If(data_requested,
                    # If we have data to send, mark first byte.
                    If(bytes_left_in_frame,
                        tx_stream.first.eq(1)
                    )
                )
            ),

            # SEND_DATA state outputs
            If(fsm.ongoing("SEND_DATA"),
                # Strobe frame_finished one cycle after we're on the last byte of the frame.
                self.frame_finished.eq(last_byte_in_frame),

                # Don't advance ... until our data is accepted.
                If(tx_stream.ready,
                    tx_stream.first.eq(0),

                    # ... and mark the relevant byte as sent.
                    bytes_left_in_frame.eq(bytes_left_in_frame - 1),
                    bytes_left_in_packet.eq(bytes_left_in_packet - 1),

                    # If we've just completed transmitting a packet...
                    If(byte_terminates_send,
                        # Move to the next DATA pid, which is always one DATA PID less.
                        next_data_pid.eq(next_data_pid - 1),

                        # Mark our next packet as being a full one.
                        bytes_left_in_packet.eq(self._max_packet_size)
                    )
                )
            ),

            # SEND_ZLP state outputs
            If(fsm.ongoing("SEND_ZLP"),
                # Nothing to do here
            )
        ]

        # Combinational output assignments to match Amaranth timing (m.d.comb +=)
        self.comb += [
            # IDLE state combinational outputs
            If(fsm.ongoing("IDLE"),
                next_byte.eq(0),

                # Strobe when a new packet starts.
                If(data_requested,
                    self.data_requested.eq(1)
                )
            ),

            # SEND_DATA state combinational outputs
            If(fsm.ongoing("SEND_DATA"),
                # Our data is always valid in this state...
                tx_stream.valid.eq(1),
                # ... and we're terminating if we're on the last byte of the packet or frame.
                tx_stream.last.eq(byte_terminates_send),

                # Producer has data available.
                If(self.stream.valid,
                    tx_stream.payload.eq(self.stream.payload)
                ),

                # Don't advance ...
                next_byte.eq(tx_cnt),
                self.stream.ready.eq(0),

                # ... until our data is accepted.
                If(tx_stream.ready,
                    # Advance to the next byte in the frame ...
                    self.stream.ready.eq(1),
                    next_byte.eq(tx_cnt + 1)
                )
            ),

            # SEND_ZLP state combinational outputs
            If(fsm.ongoing("SEND_ZLP"),
                # We'll request a ZLP by strobing LAST and VALID without strobing FIRST.
                tx_stream.valid.eq(1),
                tx_stream.last.eq(1)
            )
        ]

        # Update tx_cnt based on next_byte (synchronous)
        self.sync.usb += tx_cnt.eq(next_byte)
