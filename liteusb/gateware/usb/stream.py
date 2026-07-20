#
# This file is part of LUNA / LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Core stream definitions. """

from migen import *
from migen.genlib.fsm import NextValue

from ..stream         import StreamInterface
from ..stream.arbiter import StreamArbiter


class USBInStreamInterface(StreamInterface):
    """ Variant of LUNA's StreamInterface optimized for USB IN transmission.

    This stream interface is nearly identical to StreamInterface, with the following
    restriction: the `valid` signal _must_ be held high for every packet between `first`
    and `last`, inclusively.

    This means that the relevant interface can easily be translated to the UTMI transmit
    signals, with the following mappings:

        Stream  | UTMI
        --------|-----------
        valid   | tx_valid
        payload | tx_data
        ready   | tx_ready
    """

    def bridge_to(self, utmi_tx):
        """ Generates a list of connections that connect this stream to the provided UTMITransmitInterface. """

        return [
            utmi_tx.valid  .eq(self.valid),
            utmi_tx.data   .eq(self.payload),

            self.ready     .eq(utmi_tx.ready)
        ]



class USBOutStreamInterface(Record):
    """ Variant of LUNA's StreamInterface optimized for USB OUT receipt.

    This is a heavily simplified version of our StreamInterface, which omits the 'first',
    'last', and 'ready' signals. Instead, the streamer indicates when data is valid using
    the 'next' signal; and the receiver must keep time.

    This is selected so the relevant interface can easily be translated to the UTMI receive
    signals, with the following mappings:

        UTMI      | Stream
        --------- |-----------
        rx_active | valid
        rx_data   | payload
        rx_valid  | next

    """

    def __init__(self, payload_width=8):
        """
        Parameter:
            payload_width -- The width of the payload packets.
        """
        super().__init__([
            ('valid',    1),
            ('next',     1),

            ('payload',  payload_width),
        ])


    def bridge_to(self, utmi_rx):
        """ Generates a list of connections that connect this stream to the provided UTMIReceiveInterface. """

        return [
            self.valid     .eq(utmi_rx.rx_active),
            self.next      .eq(utmi_rx.rx_valid),
            self.data      .eq(utmi_rx.payload)
        ]


    def stream_eq(self, other):
        """ Connects this stream to ``other``; data flows self -> other.

        Matches LUNA's stream_eq direction semantics: ``a.stream_eq(b)``
        drives stream ``b`` from stream ``a``.
        """
        return [
            other.valid.eq(self.valid),
            other.next.eq(self.next),
            other.payload.eq(self.payload),
        ]




class USBOutStreamBoundaryDetector(Module):
    """ Gateware that detects USBOutStream packet boundaries, and generates First and Last signals.

    As UTMI/ULPI do not denote the last byte of a packet; this module injects two bytes of delay in
    order to correctly identify the last bytes.

    Attributes
    ----------
    unprocessed_stream: USBOutStreamInterface, input stream
        The stream to work with; will be processed and then output on :attr:``processed_stream``.
    processed_stream: USBOutStreamInterface, output stream
        The stream produced by this module. This stream is two bytes delayed from :attr:``unprocessed_stream``;
        and in-phase with the :attr::``first`` and :attr::``last`` signals.

    complete_in: Signal(), input, optional
        Input that accepts an RxComplete signal. If provided; a delayed version will be produced on
        :attr:``complete_out`` after a :attr:``processed_stream`` packet terminates.
    invalid_in: Signal(), input, optional
        Input that accepts an RxInvalid signal. If provided; a delayed version will be produced on
        :attr:``complete_out`` after a :attr:``processed_stream`` packet terminates.


    complete_out: Signal(), output
        If :attr:``complete_in`` is provided; this signal provides a delayed version of that signal
        timed so it is strobed after :attr:``processed_stream`` packets complete.
    invalid_out: Signal(), output
        If :attr:``invalid_out`` is provided; this signal provides a delayed version of that signal
        timed so it is strobed after :attr:``processed_stream`` packets complete.

    first: Signal(), output
        Indicates that the byte present on :attr:``processed_stream`` is the first byte of a packet.
    last: Signal(), output
        Indicates that the byte present on :attr:``processed_stream`` is the last byte of a packet.

    Parameters
    ----------
    domain: str
        The name of the domain the stream belongs to; defaults to "usb".
    """

    def __init__(self, domain="usb"):

        self._domain = domain

        #
        # I/O port
        #
        self.unprocessed_stream = USBOutStreamInterface()
        self.processed_stream   = USBOutStreamInterface()

        self.complete_in        = Signal()
        self.invalid_in         = Signal()

        self.complete_out       = Signal()
        self.invalid_out        = Signal()

        self.first              = Signal()
        self.last               = Signal()

        #
        # Internal logic
        #
        in_stream  = self.unprocessed_stream
        out_stream = self.processed_stream

        # We'll buffer a single byte of the stream, so we can always be one byte ahead.
        buffered_byte = Signal(8)
        is_first_byte = Signal()

        buffered_complete = Signal()
        buffered_invalid  = Signal()

        # Create the finite state machine in the usb domain
        fsm = FSM(reset_state="WAIT_FOR_FIRST_BYTE")
        # Explicitly place FSM in the usb domain to match the test clock
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules += fsm

        # WAIT_FOR_FIRST_BYTE -- we're not actively receiving data, yet. Wait for the
        # first byte of a new packet.
        fsm.act("WAIT_FOR_FIRST_BYTE",
            # Once we've received our first byte, buffer it, and mark it as our first byte.
            If(in_stream.valid & in_stream.next,
                NextValue(buffered_byte, in_stream.payload),
                NextValue(is_first_byte, 1),
                NextState("RECEIVE_AND_TRANSMIT")
            )
        )

        # RECEIVE_AND_TRANSMIT -- receive incoming bytes, and transmit our buffered bytes.
        # We'll transmit one byte per byte received; ensuring we always retain a single byte --
        # our last byte.
        fsm.act("RECEIVE_AND_TRANSMIT",
            # Buffer any complete/invalid signals we get while receiving, so we don't output
            # them before we finish outputting our processed stream.
            NextValue(buffered_complete, buffered_complete | self.complete_in),
            NextValue(buffered_invalid, buffered_invalid | self.invalid_in),

            # If we get a new byte, emit our buffered byte, and store the incoming byte.
            If(in_stream.valid & in_stream.next,
                # ... and store the new, incoming byte.
                NextValue(buffered_byte, in_stream.payload),
                NextValue(is_first_byte, 0)
            ),

            # Once we no longer have an active packet, transmit our _last_ byte,
            # and move back to waiting for an active packet.
            If(~in_stream.valid,
                NextValue(is_first_byte, 0),
                NextState("OUTPUT_STROBES")
            )
        )

        fsm.act("OUTPUT_STROBES",
            NextValue(buffered_complete, 0),
            NextValue(buffered_invalid, 0),
            NextState("WAIT_FOR_FIRST_BYTE")
        )

        # Synchronous output assignments to match Amaranth timing
        # These implement the same behavior as Amaranth's m.d.usb +=
        self.sync.usb += [
            # Default assignments
            out_stream.valid.eq(0),
            out_stream.next.eq(0),
            self.first.eq(0),
            self.last.eq(0),
            self.complete_out.eq(0),
            self.invalid_out.eq(0),

            # WAIT_FOR_FIRST_BYTE state outputs
            If(fsm.ongoing("WAIT_FOR_FIRST_BYTE"),
                out_stream.valid.eq(0),
                self.first.eq(0),
                self.last.eq(0),
                out_stream.next.eq(0),
                self.complete_out.eq(0),
                self.invalid_out.eq(0)
            ),

            # RECEIVE_AND_TRANSMIT state outputs
            If(fsm.ongoing("RECEIVE_AND_TRANSMIT"),
                out_stream.valid.eq(1),
                out_stream.next.eq(0),

                # If we get a new byte, emit our buffered byte
                If(in_stream.valid & in_stream.next,
                    out_stream.payload.eq(buffered_byte),
                    out_stream.next.eq(1),
                    self.first.eq(is_first_byte)
                ),

                # Once we no longer have an active packet, transmit our _last_ byte
                If(~in_stream.valid,
                    out_stream.payload.eq(buffered_byte),
                    out_stream.next.eq(1),
                    self.first.eq(is_first_byte),
                    self.last.eq(1)
                )
            ),

            # OUTPUT_STROBES state outputs
            If(fsm.ongoing("OUTPUT_STROBES"),
                self.complete_out.eq(buffered_complete),
                self.invalid_out.eq(buffered_invalid)
            )
        ]


class USBRawSuperSpeedStream(StreamInterface):
    """ Variant of LUNA's StreamInterface optimized for carrying raw USB3 data.

    Low-level USB3 data-streams consist of both data bytes ("data") and control flags,
    which differentiate standard data bytes from data bytes used for control.

    This variant comes implicitly with the relevant control flags; and is sized to allow
    gearing that makes USB3's high-speed signals manageable.

    Parameters
    ----------
    payload_words: int
        The number of payload words (1 byte data, 1 bit control) to include in the current stream.
    """

    def __init__(self, payload_words=4):
        super().__init__(payload_width=8 * payload_words, extra_fields=[('ctrl', payload_words)])


    def stream_eq(self, interface, *, endian_swap=False, omit=None, **kwargs):
        """ Extend the global ``stream_eq`` operator to swap endianness. """

        # If we're not performing an endian swap, delegate directly to our parent.
        if endian_swap == False:
            return super().stream_eq(interface, omit=omit, **kwargs)

        # Otherwise, perform our full endian swap.

        if omit is None:
            omit = []

        # Add ``data`` and ``ctrl`` to the list of fields to omit, as we'll
        # create those connection operations ourselves.
        omit = [*omit, 'code', 'data']

        # Gather the operations used to perform the basic ``stream_eq``...
        operations = super().stream_eq(interface, omit=omit, **kwargs)

        # ... and then add the operations necessary to connect our data/ctrl,
        # with endianness swaps.
        payload_words = len(self.ctrl)
        for i in range(payload_words):

            # Figure out what word we want to grab from, on the RHS.
            # It only matters that this is the word opposite of the word we're reading in the LHS.
            rhs_word_index = (payload_words - i) -1

            # Create the operations necessary to perform our assignment with our endian swap...
            endian_swap_operations = [
                self.data.word_select(i, 8)  .eq(interface.data.word_select(rhs_word_index, 8)),
                self.ctrl[i]                 .eq(interface.ctrl[rhs_word_index])
            ]

            #... and add it to our overall list of operations.
            operations.extend(endian_swap_operations)


        return operations



class SuperSpeedStreamArbiter(StreamArbiter):
    """ Convenience variant of our StreamArbiter that operates SuperSpeed streams in the ``ss`` domain. """

    def __init__(self):
        super().__init__(stream_type=USBRawSuperSpeedStream, domain="ss")


class SuperSpeedStreamInterface(StreamInterface):
    """ Convenience variant of our StreamInterface sized to work with SuperSpeed streams. """

    def __init__(self):
        super().__init__(payload_width=32, valid_width=4)
