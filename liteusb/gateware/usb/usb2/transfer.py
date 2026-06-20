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
from migen.genlib.fsm import FSM, NextState, NextValue
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
        self.specials += buffer_write_ports + buffer_read_ports

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
        stream_ended_in_buffer0 = Signal(name="stream_ended_in_buffer0")
        stream_ended_in_buffer1 = Signal(name="stream_ended_in_buffer1")
        buffer_stream_ended = [stream_ended_in_buffer0, stream_ended_in_buffer1]

        # Create values equivalent to the buffer numbers for our read and write buffer; which switch
        # whenever we swap our two buffers.
        # Capture the current value of buffer_toggle for buffer selection. A separate registered
        # copy (prev_toggle) is used when the same sync statement also toggles buffer_toggle,
        # so that stream-ended flag updates are resolved with the pre-toggle value.
        current_toggle = Signal()
        self.comb += current_toggle.eq(self.buffer_toggle)
        prev_toggle = Signal()
        self.sync.usb += prev_toggle.eq(self.buffer_toggle)

        write_buffer_number = current_toggle
        read_buffer_number = ~current_toggle

        # Create shortcuts to active fill_count / stream_ended signals for the buffer being written.
        write_fill_count = Array(buffer_fill_count)[write_buffer_number]
        write_stream_ended = Mux(current_toggle, stream_ended_in_buffer1, stream_ended_in_buffer0)

        # Create shortcuts to the fill_count / stream_ended signals for the packet being sent.
        read_fill_count = Array(buffer_fill_count)[read_buffer_number]
        read_stream_ended = Mux(current_toggle, stream_ended_in_buffer0, stream_ended_in_buffer1)

        self.debug_stream_ended_0 = stream_ended_in_buffer0
        self.debug_stream_ended_1 = stream_ended_in_buffer1
        self.debug_fill_count_0 = buffer_fill_count[0]
        self.debug_fill_count_1 = buffer_fill_count[1]
        self.debug_read_fill_count = read_fill_count
        self.debug_read_stream_ended = read_stream_ended

        # Keep track of our current send position; which determines where we are in the packet.
        send_position = Signal(max=self._max_packet_size + 1)

        # Shortcut names.
        in_stream = self.transfer_stream
        out_stream = self.packet_stream

        self.debug_last_and_toggle = Signal()
        self.sync.usb += If(in_stream.last & self.buffer_toggle, self.debug_last_and_toggle.eq(1))
        self.debug_comb_last_and_toggle = Signal()
        self.comb += self.debug_comb_last_and_toggle.eq(in_stream.last & self.buffer_toggle)
        self.debug_wfd_set_se1 = Signal()
        self.debug_wfd_clear_se0 = Signal()
        self.debug_wfd_clear_se1 = Signal()
        self.debug_prev_toggle = Signal()
        self.comb += self.debug_prev_toggle.eq(prev_toggle)
        self.debug_wfd_comb = Signal()
        self.debug_se1_test = Signal()
        # will assign below after fsm defined

        # Create write enable signal
        buffer_0_we = in_stream.valid & in_stream.ready & ~current_toggle
        buffer_1_we = in_stream.valid & in_stream.ready & current_toggle

        # Set both fill counts and stream-ended flags to zero when we discard data.
        self.sync.usb += If(self.discard,
            buffer_fill_count[0].eq(0),
            buffer_fill_count[1].eq(0),
            buffer_stream_ended[0].eq(0),
            buffer_stream_ended[1].eq(0),
        )

        # Increment our fill count whenever we accept new data.
        self.sync.usb += [
            If(~self.discard & buffer_0_we,
                buffer_fill_count[0].eq(buffer_fill_count[0] + 1)
            ),
            If(~self.discard & buffer_1_we,
                buffer_fill_count[1].eq(buffer_fill_count[1] + 1)
            )
        ]

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
            # Drive write enable for both ports - only the active buffer will actually write
            buffer_write_ports[0].we.eq(in_stream.valid & in_stream.ready & ~current_toggle),
            buffer_write_ports[1].we.eq(in_stream.valid & in_stream.ready & current_toggle)
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
        fsm = ClockDomainsRenamer("usb")(fsm)
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

                # Swap read/write buffers and toggle the data PID.
                NextValue(self.buffer_toggle, ~self.buffer_toggle),
                NextValue(self.data_pid[0], ~self.data_pid[0])
            )
        )

        # WAIT_TO_SEND -- we now have at least a buffer full of data to send; we'll
        # need to wait for an IN token to send it.
        fsm.act("WAIT_TO_SEND",
            NextValue(send_position, 0),

            # If discarding data, go back to waiting for new data.
            If(self.discard,
                NextState("WAIT_FOR_DATA")

            # If we get a clear halt request while in this state, reset to initial PID.
            ).Elif(self.reset_sequence,
                NextState("WAIT_TO_SEND")

            # Otherwise, once we get an IN token, move to sending a packet.
            ).Elif(in_token_received,
                # If we have a packet to send, send it.
                If(read_fill_count,
                    NextState("SEND_PACKET"),
                    NextValue(out_stream.first, 1)

                # Otherwise, we entered a transmit path without any data in the buffer.
                ).Else(
                    # Send a ZLP...
                    out_stream.valid.eq(1),
                    out_stream.last.eq(1),
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
                NextValue(send_position, send_position + 1),
                NextValue(out_stream.first, 0),

                # If we've just sent our last packet, we're now ready to wait for a
                # response from our host.
                If(last_packet,
                    NextState("WAIT_FOR_ACK")
                )
            )
        )

        # Pre-fetch the next byte while sending.
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
                # Figure out if we'll need to follow up with a ZLP. If we have ZLP generation enabled,
                # we'll make sure we end on a short packet. If this is max-packet-size packet _and_ our
                # transfer ended with this packet; we'll need to inject a ZLP.
                If(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended,
                    # If we're following up with a ZLP, move back to our "wait to send" state.
                    # Since we've now cleared our fill count; this next go-around will emit a ZLP.
                    NextState("WAIT_TO_SEND")

                # Otherwise, there's a possibility we already have a packet-worth of data waiting
                # for us in our "write buffer", which we've been filling in the background.
                # If this is the case, we'll flip which buffer we're working with, toggle our data pid,
                # and then ready ourselves for transmit.
                ).Elif(~in_stream.ready | packet_ready,
                    NextState("WAIT_TO_SEND")

                # If neither of the above conditions are true; we now don't have enough data to send.
                # We'll wait for enough data to transmit.
                ).Else(
                    NextState("WAIT_FOR_DATA")
                )
            ),

            # If the host starts a new packet without ACK'ing, we'll need to retransmit, unless discarding.
            # We'll move back to our "wait for token" state without clearing our buffer.
            If(self.tokenizer.new_token & ~self.discard,
                NextState("WAIT_TO_SEND")
            )
        )

        #
        # Synchronous updates based on FSM state - these need to be in sync.usb domain
        # (In Amaranth, these were m.d.usb += inside the FSM states)
        #

        # WAIT_FOR_ACK: On ACK, clear read_fill_count and handle ZLP/data-ready.
        # All logic in one sync block so the ZLP condition is evaluated using
        # the pre-clear value of read_fill_count (migen's eval() reads from
        # committed signal_values, not pending modifications).
        self.sync.usb += If(fsm.ongoing("WAIT_FOR_ACK") & self.handshakes_in.ack,
            read_fill_count.eq(0),
            If(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended,
                self.data_pid[0].eq(~self.data_pid[0])
            ).Elif(~in_stream.ready | packet_ready,
                self.buffer_toggle.eq(~self.buffer_toggle),
                self.data_pid[0].eq(~self.data_pid[0])
            )
        )

        # WAIT_TO_SEND: When discarding, undo data_pid toggle
        self.sync.usb += If(fsm.ongoing("WAIT_TO_SEND") & self.discard,
            self.data_pid[0].eq(~self.data_pid[0])
        )

        # WAIT_TO_SEND: When reset_sequence, reset to initial PID
        self.sync.usb += If(fsm.ongoing("WAIT_TO_SEND") & self.reset_sequence,
            self.data_pid.eq(self.start_with_data1)
        )

        # Capture whether a byte was accepted into each buffer, and whether it was the LAST
        # byte of the stream. We set the stream-ended flag one cycle later, after any
        # WAIT_FOR_DATA buffer toggle has taken effect. This avoids Migen simulation ordering
        # issues where the flag would otherwise be written into the wrong buffer.
        buffer_0_we_r = Signal()
        buffer_1_we_r = Signal()
        in_last_r = Signal()
        self.sync.usb += [
            buffer_0_we_r.eq(buffer_0_we),
            buffer_1_we_r.eq(buffer_1_we),
            in_last_r.eq(in_stream.last)
        ]

        # Compute set/clear for each stream-ended bit and update the whole register at once.
        # Updating the full 2-bit register avoids Migen bit-slice simulation problems.
        set_ended = Signal(2)
        clear_ended = Signal(2)
        self.comb += [
            set_ended[0].eq(in_last_r & buffer_0_we_r),
            set_ended[1].eq(in_last_r & buffer_1_we_r),
            clear_ended[0].eq(self.discard |
                (fsm.ongoing("WAIT_FOR_ACK") & self.handshakes_in.ack & (~in_stream.ready | packet_ready) &
                 ~(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended) &
                 current_toggle) |
                (fsm.ongoing("WAIT_TO_SEND") & in_token_received & ~read_fill_count & current_toggle)),
            clear_ended[1].eq(self.discard |
                (fsm.ongoing("WAIT_FOR_ACK") & self.handshakes_in.ack & (~in_stream.ready | packet_ready) &
                 ~(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended) &
                 ~current_toggle) |
                (fsm.ongoing("WAIT_TO_SEND") & in_token_received & ~read_fill_count & ~current_toggle)),
        ]
        # Set/clear stream-ended flags in a single sync block using separate signals. This
        # avoids Migen's simulator issues with multiple drivers or read-modify-write on a
        # multi-bit register.
        clear_ended0 = self.discard | \
            (fsm.ongoing("WAIT_FOR_ACK") & self.handshakes_in.ack & (~in_stream.ready | packet_ready) &
             ~(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended) &
             current_toggle) | \
            (fsm.ongoing("WAIT_TO_SEND") & in_token_received & ~read_fill_count & current_toggle)
        clear_ended1 = self.discard | \
            (fsm.ongoing("WAIT_FOR_ACK") & self.handshakes_in.ack & (~in_stream.ready | packet_ready) &
             ~(self.generate_zlps & (read_fill_count == self._max_packet_size) & read_stream_ended) &
             ~current_toggle) | \
            (fsm.ongoing("WAIT_TO_SEND") & in_token_received & ~read_fill_count & ~current_toggle)

        self.sync.usb += [
            stream_ended_in_buffer0.eq((stream_ended_in_buffer0 | set_ended[0]) & ~clear_ended0),
            stream_ended_in_buffer1.eq((stream_ended_in_buffer1 | set_ended[1]) & ~clear_ended1)
        ]
        self.debug_set_ended = Signal(2)
        self.debug_clear_ended = Signal(2)
        self.debug_buf_we_r = Signal(2)
        self.debug_in_last_r = Signal()
        self.comb += [
            self.debug_set_ended.eq(set_ended),
            self.debug_clear_ended.eq(clear_ended),
            self.debug_se1_test.eq(set_ended[0]),
            self.debug_wfd_set_se1.eq(set_ended[1]),
            self.debug_buf_we_r.eq(Cat(buffer_0_we_r, buffer_1_we_r)),
            self.debug_in_last_r.eq(in_last_r)
        ]

        self.comb += self.debug_wfd_comb.eq(fsm.ongoing("WAIT_FOR_DATA") & packet_ready & in_stream.last & prev_toggle)


