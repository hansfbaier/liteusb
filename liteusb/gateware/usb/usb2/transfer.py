#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""
This module contains gateware designed to assist with endpoint/transfer state management.
Its components facilitate data transfer longer than a single packet.
"""

from migen import *
from migen.genlib.fsm import FSM, NextState
from migen.genlib.record import Record

from .packet import HandshakeExchangeInterface, TokenDetectorInterface
from ..stream import USBInStreamInterface
from ...stream import StreamInterface


class USBInTransferManager(Module):
    """ Sequencer that converts a long data stream (a USB *transfer*) into a burst of USB packets.

    This module is designed so it can serve as the core of a IN endpoint.

    Attributes
    ----------

    active: Signal(), input
        Held high to enable this module to send packets to the host, and interpret tokens from the host.
        This is typically equivalent to the relevant endpoint being addressed by the host.

    transfer_stream: StreamInterface, input stream
        Input stream; accepts transfer data to be sent on the endpoint. This stream represents
        a USB transfer, and can be as long as is desired; and will be sent in max-packet-size chunks.

        For this stream: ``first`` is ignored; and thus entirely optional. ``last`` is optional;
        if it is not provided; this module will send only max-length-packets, sending a new packet
        every time a full packet size is reached.
    packet_stream: USBInStreamInterface, output stream
        Output stream; broken into packets to be sent.

    flush: Signal(), input
        If high, data that is currently buffered will be sent out as soon as possible. The module will
        not wait further for the input stream to end, or to have a max-length packet; it will send a
        packet as soon as possible.

    discard: Signal(), input
        If high, data that is currently buffered will be discarded. The module will not buffer further
        data, or send any further packets, until this signal is low again.

    data_pid: Signal(2), output
        The LSBs of the data PID to be issued with the current packet. Used with :attr:`packet_stream`
        to indicate the PID of the transmitted packet.

    tokenizer: TokenDetectorInterface, input
        Connection to a detector that detects incoming tokens packets.

    handshakes_in: HandshakeExchangeInterface, input
        Indicates when handshakes are received from the host.
    handshakes_out: HandshakeExchangeInterface, output
        Output that carries handshake packet requests.

    generate_zlps: Signal(), input
        If high, zero-length packets will automatically be generated if the end of a transfer would
        not result in a short packet. (This should be set for control endpoints; and for any interface
        where transfer boundaries are significant.)

    start_with_data1: Signal(), input
        If high, the transmitter will start our PID with DATA1
    reset_sequence: Signal(), input
        If true, our PID generated will reset to the value indicated by `start_with_data1`.
        If desired, this can be held permanently high to control our PID expectation manually.

    Parameters
    ----------
    max_packet_size: int
        The maximum packet size for our associated endpoint, in bytes.
    """

    def __init__(self, max_packet_size):

        self._max_packet_size = max_packet_size

        #
        # I/O port
        #
        self.active           = Signal()

        self.transfer_stream  = StreamInterface()
        self.packet_stream    = USBInStreamInterface()

        self.flush            = Signal()
        self.discard          = Signal()

        # Note: we'll start with DATA1 in our register; as we'll toggle our data PID
        # before we send.
        self.data_pid         = Signal(2, reset=1)
        self.buffer_toggle    = Signal()

        self.tokenizer        = TokenDetectorInterface()
        self.handshakes_in    = HandshakeExchangeInterface(is_detector=True)
        self.handshakes_out   = HandshakeExchangeInterface(is_detector=False)

        self.generate_zlps    = Signal()
        self.start_with_data1 = Signal()
        self.reset_sequence   = Signal()

        #
        # Transciever state.
        #


        # Handle our PID-sequence reset.
        # Note that we store the _inverse_ of our data PID, as we'll toggle our DATA PID
        # before sending. However, if it has already been toggled then this is overridden below.
        self.sync.usb += If(self.reset_sequence,
            self.data_pid.eq(~self.start_with_data1)
        )

        #
        # Transmit buffer.
        #
        # Our USB connection imposed a few requirements on our stream:
        # 1) we must be able to transmit packets at a full rate; i.e.
        #    must be asserted from the start to the end of our transfer; and
        # 2) we must be able to re-transmit data if a given packet is not ACK'd.
        #
        # Accordingly, we'll buffer a full USB packet of data, and then transmit
        # it once either a) our buffer is full, or 2) the transfer ends (last=1).
        #
        # This implementation is double buffered; so a buffer fill can be pipelined
        # with a transmit.
        #

        # We'll create two buffers; so we can fill one as we empty the other.
        transmit_buffer_0 = Memory(8, self._max_packet_size)
        transmit_buffer_1 = Memory(8, self._max_packet_size)
        self.specials += [transmit_buffer_0, transmit_buffer_1]
        
        buffer = [transmit_buffer_0, transmit_buffer_1]
        buffer_write_ports = [
            buffer[i].get_port(write_capable=True, clock_domain="usb") for i in range(2)
        ]
        buffer_read_ports = [
            buffer[i].get_port(clock_domain="usb") for i in range(2)
        ]
        # Memory ports are part of the Memory special, don't add to submodules

        # Create values equivalent to the buffer numbers for our read and write buffer; which switch
        # whenever we swap our two buffers.
        write_buffer_number = self.buffer_toggle
        read_buffer_number = ~self.buffer_toggle

        # Create a shorthand that refers to the buffer to be filled; and the buffer to send from.
        # We'll call these the Read and Write buffers.
        buffer_write = Array(buffer_write_ports)[write_buffer_number]
        buffer_read = Array(buffer_read_ports)[read_buffer_number]

        # Buffer state tracking:
        # - Our ``fill_count`` keeps track of how much data is stored in a given buffer.
        # - Our ``stream_ended`` bit keeps track of whether the stream ended while filling up
        #   the given buffer. This indicates that the buffer cannot be filled further; and, when
        #   ``generate_zlps`` is enabled, is used to determine if the given buffer should end in
        #   a short packet; which determines whether ZLPs are emitted.
        buffer_fill_count = [
            Signal(max=self._max_packet_size + 1, name=f"fill_count_{i}") for i in range(2)
        ]
        buffer_stream_ended = [
            Signal(name=f"stream_ended_in_buffer{i}") for i in range(2)
        ]

        # Create shortcuts to active fill_count / stream_ended signals for the buffer being written.
        write_fill_count = Array(buffer_fill_count)[write_buffer_number]
        write_stream_ended = Array(buffer_stream_ended)[write_buffer_number]

        # Create shortcuts to the fill_count / stream_ended signals for the packet being sent.
        read_fill_count = Array(buffer_fill_count)[read_buffer_number]
        read_stream_ended = Array(buffer_stream_ended)[read_buffer_number]

        # Keep track of our current send position; which determines where we are in the packet.
        send_position = Signal(max=self._max_packet_size + 1)

        # Shortcut names.
        in_stream = self.transfer_stream
        out_stream = self.packet_stream

        # Set both fill counts to zero when we discard data.
        self.sync.usb += If(self.discard,
            write_fill_count.eq(0),
            write_stream_ended.eq(0),
            read_fill_count.eq(0),
            read_stream_ended.eq(0),
        )

        # Increment our fill count whenever we accept new data.
        self.sync.usb += If(~self.discard & buffer_write.we,
            write_fill_count.eq(write_fill_count + 1)
        )

        # If the stream ends while we're adding data to the buffer, mark this as an ended stream.
        self.sync.usb += If(in_stream.last & buffer_write.we,
            write_stream_ended.eq(1)
        )


        # Use our memory's two ports to capture data from our transfer stream; and two emit packets
        # into our packet stream. Since we'll never receive to anywhere else, or transmit to anywhere else,
        # we can just unconditionally connect these.
        self.comb += [
            # We'll only ever -write- data from our input stream...
            buffer_write_ports[0].adr.eq(write_fill_count),
            buffer_write_ports[0].dat_w.eq(in_stream.payload),
            buffer_write_ports[1].adr.eq(write_fill_count),
            buffer_write_ports[1].dat_w.eq(in_stream.payload),

            # ... and we'll only ever -send- data from the Read buffer.
            buffer_read.adr.eq(send_position),
            out_stream.payload.eq(buffer_read.dat_r),

            # We're ready to receive data iff we have space in the buffer we're currently filling.
            in_stream.ready.eq((write_fill_count != self._max_packet_size) & ~write_stream_ended),
            buffer_write.we.eq(in_stream.valid & in_stream.ready)
        ]

        # A packet is completing when:
        # - There is a byte arriving from the input stream, and:
        # - It is either the last byte from the stream, or will cause us to reach max packet size.
        packet_nearly_full = (write_fill_count + 1 == self._max_packet_size)
        packet_completing = in_stream.valid & (in_stream.last | packet_nearly_full)

        # We should also send a packet when both:
        # - Our flush input is asserted
        # - We have some data in the buffer to flush.
        packet_to_flush = self.flush & (write_fill_count != 0)

        # We're ready to send a packet when either of the above conditions is met, and not discarding.
        packet_ready = (packet_completing | packet_to_flush) & ~self.discard

        # Shortcut for when we need to deal with an in token.
        # Pulses high an interpacket delay after receiving an IN token.
        in_token_received = self.active & self.tokenizer.is_in & self.tokenizer.ready_for_response

        # Signal to track if this is the last packet being sent
        last_packet = Signal()

        #
        # Main FSM
        #
        fsm = FSM(reset_state="WAIT_FOR_DATA")
        self.submodules.fsm = fsm

        # WAIT_FOR_DATA -- We don't yet have a full packet to transmit, so  we'll capture data
        # to fill the our buffer. At full throughput, this state will never be reached after
        # the initial post-reset fill.
        fsm.act("WAIT_FOR_DATA",
            # We can't yet send data; so NAK any packet requests.
            self.handshakes_out.nak.eq(in_token_received),

            # If we've just finished a packet, we now have data we can send!
            If(packet_ready,
                NextState("WAIT_TO_SEND"),
                self.buffer_toggle.eq(~self.buffer_toggle),
                self.data_pid[0].eq(~self.data_pid[0]),
                read_stream_ended.eq(0)
            )
        )

        # WAIT_TO_SEND -- we now have at least a buffer full of data to send; we'll
        # need to wait for an IN token to send it.
        fsm.act("WAIT_TO_SEND",
            send_position.eq(0),

            # If discarding data, go back to waiting for new data.
            If(self.discard,
                # Undo the data PID toggle.
                self.data_pid[0].eq(~self.data_pid[0]),
                NextState("WAIT_FOR_DATA")

            # If we get a clear halt request while in this state, reset to initial PID.
            ).Elif(self.reset_sequence,
                self.data_pid.eq(self.start_with_data1)

            # Otherwise, once we get an IN token, move to sending a packet.
            ).Elif(in_token_received,
                # If we have a packet to send, send it.
                If(read_fill_count,
                    NextState("SEND_PACKET"),
                    out_stream.first.eq(1)

                # Otherwise, we entered a transmit path without any data in the buffer.
                ).Else(
                    # Send a ZLP...
                    out_stream.valid.eq(1),
                    out_stream.last.eq(1),
                    # ... and clear the need to follow up with one, since we've just sent a short packet.
                    read_stream_ended.eq(0),
                    NextState("WAIT_FOR_ACK")
                )
            )
        )

        # SEND_PACKET -- Send the buffered data
        fsm.act("SEND_PACKET",
            last_packet.eq(send_position + 1 == read_fill_count),

            out_stream.valid.eq(1),
            out_stream.last.eq(last_packet),

            # Once our transmitter accepts our data...
            If(out_stream.ready,
                send_position.eq(send_position + 1),
                out_stream.first.eq(0),

                # If we've just sent our last packet, we're now ready to wait for a
                # response from our host.
                If(last_packet,
                    NextState("WAIT_FOR_ACK")
                )
            )
        )
        
        # Need to update buffer_read.adr combinatorially when sending
        self.comb += If(fsm.ongoing("SEND_PACKET") & out_stream.ready,
            buffer_read.adr.eq(send_position + 1)
        )

        # WAIT_FOR_ACK -- We've just sent a packet; but don't know if the host has
        # received it correctly. We'll wait to see if the host ACKs.
        fsm.act("WAIT_FOR_ACK",
            # If discarding data, go back to waiting for new data.
            If(self.discard,
                NextState("WAIT_FOR_DATA")

            # If the host does ACK...
            ).Elif(self.handshakes_in.ack,
                # ... clear the data we've sent from our buffer.
                read_fill_count.eq(0),

                # Figure out if we'll need to follow up with a ZLP. If we have ZLP generation enabled,
                # we'll make sure we end on a short packet. If this is max-packet-size packet _and_ our
                # transfer ended with this packet; we'll need to inject a ZLP.
                If(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended,
                    # If we're following up with a ZLP, move back to our "wait to send" state.
                    # Since we've now cleared our fill count; this next go-around will emit a ZLP.
                    self.data_pid[0].eq(~self.data_pid[0]),
                    NextState("WAIT_TO_SEND")

                # Otherwise, there's a possibility we already have a packet-worth of data waiting
                # for us in our "write buffer", which we've been filling in the background.
                # If this is the case, we'll flip which buffer we're working with, toggle our data pid,
                # and then ready ourselves for transmit.
                ).Elif(~in_stream.ready | packet_ready,
                    NextState("WAIT_TO_SEND"),
                    self.buffer_toggle.eq(~self.buffer_toggle),
                    self.data_pid[0].eq(~self.data_pid[0]),
                    read_stream_ended.eq(0)

                # If neither of the above conditions are true; we now don't have enough data to send.
                # We'll wait for enough data to transmit.
                ).Else(
                    NextState("WAIT_FOR_DATA")
                ),

                # If the host starts a new packet without ACK'ing, we'll need to retransmit, unless discarding.
                # We'll move back to our "wait for token" state without clearing our buffer.
                If(self.tokenizer.new_token & ~self.discard,
                    NextState("WAIT_TO_SEND")
                )
            )
        )
