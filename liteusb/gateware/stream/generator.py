#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Stream generators. """

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue
from migen.genlib.record import Record


class StreamInterface(Record):
    """ Simple record implementing a unidirectional data stream.

    This class is similar to LiteX's streams.

    Attributes
    -----------
    valid: Signal(), from originator
        Indicates that the current payload bytes are valid pieces of the current transaction.
    first: Signal(), from originator
        Indicates that the payload byte is the first byte of a new packet.
    last: Signal(), from originator
        Indicates that the payload byte is the last byte of the current packet.
    payload: Signal(payload_width), from originator
        The data payload to be transmitted.

    ready: Signal(), from receiver
        Indicates that the receiver will accept the payload byte at the next active
        clock edge. Can be de-asserted to put backpressure on the transmitter.

    Parameters
    ----------
    payload_width: int
        The width of the stream's payload, in bits.
    """

    def __init__(self, payload_width=8, valid_width=1):
        super().__init__([
            ('valid', valid_width),
            ('ready', 1),
            ('first', 1),
            ('last', 1),
            ('payload', payload_width),
        ])

    @property
    def data(self):
        """ Allow 'data' to be a semantic alias for payload. """
        return self.payload


class ConstantStreamGenerator(Module):
    """ Gateware that generates stream of constant data.

    Attributes
    ----------
    start: Signal(), input
        Strobe that indicates when the stream should be started.
    done: Signal(), output
        Strobe that pulses high when we're finishing a transmission.

    start_position: Signal(range(len(data)), input
        Specifies the starting position in the constant stream; applied when start() is pulsed.

    max_length: Signal(max_length_width), input
        The maximum length to be sent -in bytes-. Defaults to the length of the stream.
        Only present if the `max_length_width` parameter is provided on creation.
    output_length: Signal(max_length_width), output
        Indicates the actual data length for the stream currently being output.
        Will always be the lesser of our data length and :attr:``max_length``.
        Only present if the `max_length_width` parameter is provided on creation.

    stream: stream_type(), output stream
        The generated stream interface.

    Parameters
    ----------
    constant_data: bytes, or equivalent
        The constant data for the stream to be generated.
        Should be an iterable of integers; or, if data_width is divisible by 8, a bytes-like object.
    domain: string
        The clock domain this generator should belong to. Defaults to 'sync'.
    stream_type: StreamInterface, or subclass
        The type of stream we'll be multiplexing.
    data_width: int, optional
        The width of the constant payload. If not provided; will be taken from the stream's payload width.
    max_length_width: int
        If provided, a `max_length` signal will be present that can limit the total length transmitted.
    data_endianness: little
        If bytes are provided, and our data width is greater
    """

    def __init__(self, constant_data, domain="sync", stream_type=StreamInterface,
            max_length_width=None, data_width=None, data_endianness="little"):

        self._domain = domain
        self._data = constant_data
        self._data_length = len(constant_data)
        self._endianness = data_endianness
        self._max_length_width = max_length_width

        #
        # I/O port.
        #
        self.start = Signal()
        self.done = Signal()

        # If we have a data width, apply it to our stream type; otherwise, use its defaults.
        if data_width:
            self.stream = stream_type(payload_width=data_width)
            self._data_width = data_width
        else:
            self.stream = stream_type()
            self._data_width = len(self.stream.payload)

        self.start_position = Signal(max=self._data_length)

        # If we have a maximum length width, include it in our I/O port.
        # Otherwise, use a constant.
        if max_length_width:
            self.max_length = Signal(max_length_width)
            self.output_length = Signal(max_length_width)
        else:
            self.max_length = self._data_length

        #
        # Internal signals
        #

        # Figure out the shape of our data.
        data_initializer, valid_bits_last_word = self._get_initializer_value()
        data_length = len(data_initializer)

        # Create ROM using Memory
        self.specials.rom = Memory(self._data_width, data_length, init=data_initializer, name="rom")
        rom_read_port = self.rom.get_port(clock_domain=self._domain)
        self.specials += rom_read_port

        if self._max_length_width:
            # Register maximum length, to improve timing.
            max_length = Signal(max_length_width)
        else:
            max_length = self.max_length

        # Register that stores our current position in the stream.
        position_in_stream = Signal(max=data_length)

        # If we have a maximum length we're enforcing, create a counter for it.
        if self._max_length_width:
            bytes_sent = Signal(max_length_width)
            bytes_per_word = (self._data_width + 7) // 8
        else:
            bytes_sent = None
            bytes_per_word = 0

        # Track when we're on the first and last packet.
        on_first_packet = Signal()
        on_last_packet = Signal()

        self.comb += [
            on_first_packet.eq(position_in_stream == self.start_position),
        ]

        if self._max_length_width:
            self.comb += [
                on_last_packet.eq(
                    (position_in_stream == (data_length - 1)) |
                    (bytes_sent + bytes_per_word >= max_length)
                )
            ]
        else:
            self.comb += [
                on_last_packet.eq(position_in_stream == (data_length - 1))
            ]

        #
        # Figure out where we should start in our stream.
        #
        start_position = Signal(max=data_length)

        # If our starting position is greater than our data length, use our data length.
        self.comb += [
            If(self.start_position >= self._data_length,
                start_position.eq(data_length - 1)
            ).Else(
                start_position.eq(self.start_position)
            )
        ]

        #
        # Output length field.
        #

        if self._max_length_width:
            # Return our max length or the length of our data, whichever is less.
            self.comb += [
                If(max_length < self._data_length,
                    self.output_length.eq(max_length)
                ).Else(
                    self.output_length.eq(self._data_length)
                )
            ]

        #
        # Controller.
        #

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        self.comb += self.stream.valid.eq(fsm.ongoing("STREAMING"))

        # IDLE -- we're not actively transmitting.
        fsm.act("IDLE",
            # Keep ourselves at the beginning of the stream, but don't yet count.
            NextValue(position_in_stream, start_position),
            If(self._max_length_width,
                NextValue(bytes_sent, 0)
            ),

            # Latch the maximum length.
            If(self._max_length_width,
                NextValue(max_length, self.max_length)
            ),

            # Once the user requests that we start, move to our stream being valid.
            If(self.start & (self.max_length > 0),
                NextState("STREAMING")
            )
        )

        # Compute valid signal for the STREAMING state
        if len(self.stream.valid) == 1:
            # Simple case: single bit valid
            streaming_valid = Signal()
            self.comb += streaming_valid.eq(1)
        else:
            # Complex case: multi-bit valid
            streaming_valid = Signal(len(self.stream.valid))
            valid_bits = len(self.stream.valid)
            
            if self._max_length_width:
                ending_due_to_data_length = Signal()
                ending_due_to_max_length = Signal()
                valid_due_to_data_length = Signal(valid_bits)
                valid_due_to_max_length = Signal(valid_bits)
                bytes_left_over = Signal(max=bytes_per_word + 1)

                self.comb += [
                    ending_due_to_data_length.eq(position_in_stream == (data_length - 1)),
                    ending_due_to_max_length.eq(bytes_sent + bytes_per_word >= max_length),
                    valid_due_to_data_length.eq(Replicate(1, valid_bits_last_word)),
                    bytes_left_over.eq(max_length - bytes_sent),
                ]

                # Generate valid_due_to_max_length based on bytes_left_over
                cases = {}
                for i in range(1, bytes_per_word + 1):
                    cases[i] = valid_due_to_max_length.eq(Replicate(1, i))
                
                self.comb += Case(bytes_left_over, cases)

                # Final valid signal selection
                self.comb += [
                    If(on_last_packet,
                        If(ending_due_to_data_length & ending_due_to_max_length,
                            streaming_valid.eq(valid_due_to_data_length & valid_due_to_max_length)
                        ).Elif(ending_due_to_data_length,
                            streaming_valid.eq(valid_due_to_data_length)
                        ).Else(
                            streaming_valid.eq(valid_due_to_max_length)
                        )
                    ).Else(
                        streaming_valid.eq(Replicate(1, valid_bits))
                    )
                ]
            else:
                self.comb += [
                    If(on_last_packet,
                        streaming_valid.eq(Replicate(1, valid_bits_last_word))
                    ).Else(
                        streaming_valid.eq(Replicate(1, valid_bits))
                    )
                ]

        # STREAMING -- we're actively transmitting data
        fsm.act("STREAMING",
            # Always drive the stream from our current memory output...
            rom_read_port.adr.eq(position_in_stream),
            self.stream.payload.eq(rom_read_port.dat_r),

            # ... and base First and Last based on our current position in the stream.
            self.stream.first.eq(on_first_packet),
            self.stream.last.eq(on_last_packet),

            # If we have multi-bit valid, drive it
            If(len(self.stream.valid) > 1,
                self.stream.valid.eq(streaming_valid)
            ),

            # If the current data byte is accepted, move past it.
            If(self.stream.ready,
                # If there's still data left to transmit, move forward.
                If(~on_last_packet,
                    NextValue(position_in_stream, position_in_stream + 1),
                    If(self._max_length_width,
                        NextValue(bytes_sent, bytes_sent + bytes_per_word)
                    ),
                    NextState("STREAMING")
                # Otherwise, we've finished streaming. Return to DONE.
                ).Else(
                    NextState("DONE")
                )
            )
        )

        # DONE -- report our completion; and then return to idle
        fsm.act("DONE",
            self.done.eq(1),
            NextState("IDLE")
        )

    def _get_initializer_value(self):
        """ Returns this generator's data in a form usable as a ROM initializer.

        Returns
        -------
        initializer_data: interable
            An iterable suitable for use in initializing a ROM.
        valid_bytes_last_word: int
            The number of valid bits that should accompany the last word.

            For example, if we have 32-bit words; and 3 bytes of data, we'd have
            three valid bits on the last word; since the upper 8-bits are meaningless.
        """

        # If we have byte-sized data, Python will implicitly handle things correctly.
        # Return our data unmodified.
        if self._data_width == 8:
            return self._data, len(self.stream.valid)

        # If we don't have a byte-string, return our data without pre-processing.
        if not isinstance(self._data, (bytes, bytearray)):
            return self._data, len(self.stream.valid)

        # If our width isn't evenly divisible by 8, we can't accept bytes.
        if (self._data_width % 8):
            raise ValueError("Can't initialize with bytes unless data_width is divisible by 8!")

        # Figure out how wide each datum will be in bytes.
        datum_width_bytes = self._data_width // 8

        # Otherwise, we'll split it into a list of integers, manually.
        in_data = bytearray(self._data)
        out_data = []

        while in_data:
            # Extract each datum from our stream...
            datum = in_data[0:datum_width_bytes]
            del in_data[0:datum_width_bytes]

            # ... convert it into an integer ...
            datum = int.from_bytes(datum, byteorder=self._endianness)

            # ... and squish it into our output.
            out_data.append(datum)

        # Figure out how many bytes will be in our last word.
        last_word_bytes = len(self._data) % datum_width_bytes
        if last_word_bytes == 0:
            last_word_bytes = datum_width_bytes

        return out_data, last_word_bytes


class StreamSerializer(Module):
    """ Gateware that serializes a short Array input onto a stream.

    I/O port:
        I: start        -- Strobe that indicates when the stream should be started.
        O: done         -- Strobe that pulses high when we're finishing a transmission.

        I: data[]       -- The data stream to be sent out. Length is set by the data_length initializer argument.
        I: max_length[] -- The maximum length to be sent. Defaults to the length of the stream.
                           Only present if the `max_length_width` parameter is provided on creation.

        *: stream       -- The generated stream interface.

    """

    def __init__(self, data_length, domain="sync", data_width=8, stream_type=StreamInterface, max_length_width=None):
        """
        Parameters:
            data_length        -- The length of the data to be transmitted.
            domain             -- The clock domain this generator should belong to. Defaults to 'sync'.
            data_width         -- The width of the constant payload
            stream_type        -- The type of stream we'll be multiplexing. Must be a subclass of StreamInterface.
            max_length_width   -- If provided, a `max_length` signal will be present that can limit the total length
                                  transmitted.
        """

        self.domain = domain
        self.data_width = data_width
        self.data_length = data_length

        #
        # I/O port.
        #
        self.start = Signal()
        self.done = Signal()

        self.data = Array(Signal(data_width, name=f"datum_{i}") for i in range(data_length))
        self.stream = stream_type(payload_width=data_width)

        self.start_position = Signal(max=self.data_length)

        # If we have a maximum length width, include it in our I/O port.
        # Otherwise, use a constant.
        if max_length_width:
            self.max_length = Signal(max=max_length_width)
        else:
            self.max_length = self.data_length

        # Register that stores our current position in the stream.
        position_in_stream = Signal(max=self.data_length)
        if max_length_width:
            bytes_sent = Signal(max=max_length_width)
        else:
            bytes_sent = Signal(max=self.data_length)

        # Track when we're on the first and last packet.
        on_first_packet = Signal()
        on_last_packet = Signal()

        self.comb += [
            on_first_packet.eq(position_in_stream == self.start_position),
        ]

        if max_length_width:
            self.comb += [
                on_last_packet.eq(
                    (position_in_stream == (self.data_length - 1)) |
                    (bytes_sent == (self.max_length - 1))
                )
            ]
        else:
            self.comb += [
                on_last_packet.eq(position_in_stream == (self.data_length - 1))
            ]

        self.comb += [
            # Create first and last based on our stream position.
            self.stream.first.eq(on_first_packet & self.stream.valid),
            self.stream.last.eq(on_last_packet & self.stream.valid)
        ]

        #
        # Figure out where we should start in our stream.
        #
        start_position = Signal(max=self.data_length)

        # If our starting position is greater than our data length, use our data length.
        self.comb += [
            If(self.start_position >= self.data_length,
                start_position.eq(self.data_length - 1)
            ).Else(
                start_position.eq(self.start_position)
            )
        ]

        #
        # Controller.
        #
        fsm = ClockDomainsRenamer(self.domain)(FSM(reset_state="IDLE"))
        self.submodules += fsm

        self.comb += self.stream.valid.eq(fsm.ongoing("STREAMING"))

        # IDLE -- we're not actively transmitting.
        fsm.act("IDLE",
            # Keep ourselves at the beginning of the stream, but don't yet count.
            NextValue(position_in_stream, start_position),
            NextValue(bytes_sent, 0),

            # Once the user requests that we start, move to our stream being valid.
            If(self.start & (self.max_length > 0),
                NextState("STREAMING")
            )
        )

        # Explicit payload mux: the LiteX verilog backend cannot lower ArrayProxy.
        payload_mux = If(position_in_stream == 0, self.stream.payload.eq(self.data[0]))
        for i in range(1, self.data_length):
            payload_mux = payload_mux.Elif(position_in_stream == i, self.stream.payload.eq(self.data[i]))

        # STREAMING -- we're actively transmitting data
        fsm.act("STREAMING",
            payload_mux,

            # If the current data byte is accepted, move past it.
            If(self.stream.ready,
                # If there's still data left to transmit, move forward.
                If(~on_last_packet,
                    NextValue(position_in_stream, position_in_stream + 1),
                    NextValue(bytes_sent, bytes_sent + 1),
                    NextState("STREAMING")
                # Otherwise, we've finished streaming. Return to DONE.
                ).Else(
                    NextState("DONE")
                )
            )
        )

        # DONE -- report our completion; and then return to idle
        fsm.act("DONE",
            self.done.eq(1),
            NextState("IDLE")
        )
