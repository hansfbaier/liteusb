#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Contains the gateware module necessary to interpret and generate low-level USB packets. """

import operator
import functools

from migen import *
from migen.genlib.record import Record, DIR_M_TO_S, DIR_S_TO_M
from migen.genlib.fsm import FSM, NextState, NextValue

from . import USBSpeed, USBPacketID
from ...interface.utmi import UTMITransmitInterface

#
# Interfaces.
#


class HandshakeExchangeInterface(Record):
    """ Record that carries handshakes detected -or- generated between modules.

    Attributes
    ----------
    ack: Signal()
        When connected to a generator, pulsing this strobe will trigger generating of an ACK.
        When connected to a detector, this strobe will be pulsed when an ACK is detected from the host.
    nak: Signal()
        When connected to a generator, pulsing this strobe will trigger generating of an NAK.
        When connected to a detector, this strobe will be pulsed when an NAK is detected from the host.
    stall: Signal()
        When connected to a generator, pulsing this strobe will trigger generation of a STALL.
        Unused in a detector, currently.
    nyet: Signal()
        When connected to a generator, pulsing this strobe will trigger generation of a NYET.
        Unused in a detector, currently.

    Parameters
    ----------
    is_detector: bool
        If true, this will be considered an interface to a detector that identifies handshakes.
        Otherwise, this will be considered an interface to a generator that accepts handshake requests.
    """

    def __init__(self, *, is_detector):
        super().__init__([
            ('ack',   1),
            ('nak',   1),
            ('stall', 1),
            ('nyet',  1),
        ])



class DataCRCInterface(Record):
    """ Record providing an interface to a USB CRC-16 generator.

    Attributes
    ----------
    start: Signal(), input to CRC generator
        Strobe that indicates that a new CRC computation should be started.
    crc: Signal(), output from CRC generator
        The current CRC-16 value; updated with each sent or received byte.
    """

    def __init__(self):
        super().__init__([
            ('start', 1, DIR_S_TO_M),
            ('crc',   16, DIR_M_TO_S)
        ])


class TokenDetectorInterface(Record):
    """ Record providing an interface to a USB token detector.

    Attributes
    ----------
    pid: Signal(4), detector output
        The Packet ID of the most recent token.
    address: Signal(7), detector output
        The address associated with the relevant token.
    endpoint: Signal(4), detector output
        The endpoint indicated by the most recent token.

    new_token: Signal(), detector output
        Strobe asserted for a single cycle when a new token packet has been received.
    ready_for_response: Signal(), detector output
        Strobe asserted for a single cycle one inter-packet delay after a token packet is complete.
        Indicates when the token packet can be responded to.

    frame: Signal(11), detector output
        The current USB frame number.
    new_frame: Signal(), detector output
        Strobe asserted for a single cycle when a new SOF has been received.

    is_in: Signal(), detector output
        High iff the current token is an IN.
    is_out: Signal(), detector output
        High iff the current token is an OUT.
    is_setup: Signal(), detector output
        High iff the current token is a SETUP.
    is_ping: Signal(), detector output
        High iff the current token is a PING.
    """

    def __init__(self):
        super().__init__([
            ('pid',                4),
            ('address',            7),
            ('endpoint',           4),
            ('new_token',          1),
            ('ready_for_response', 1),

            ('frame',             11),
            ('new_frame',          1),

            ('is_in',              1),
            ('is_out',             1),
            ('is_setup',           1),
            ('is_ping',            1),
        ])


class InterpacketTimerInterface(Record):
    """ Record providing an interface to our interpacket timer.

    See [USB2.0: 7.1.18] and the USBInterpacketTimer gateware for more information.

    Attributes
    ----------
    start: Signal(), input to timer
        Strobe that indicates when the timer should be started. Usually started at the end of an Rx or Tx event.

    tx_allowed: Signal(), output from timer
        Strobe that goes high when it's safe to transmit after an Rx event.
    tx_timeout: Signal(), output from timer
        Strobe that goes high when the transmit-after-receive window has passed.
    rx_timeout: Signal(), output from timer
        Strobe that goes high when the receive-after-transmit window has passed.
    """

    def __init__(self):
        super().__init__([
            ('start',      1, DIR_S_TO_M),

            ('tx_allowed', 1, DIR_M_TO_S),
            ('tx_timeout', 1, DIR_M_TO_S),
            ('rx_timeout', 1, DIR_M_TO_S),
        ])


    def attach(self, *subordinates):
        """ Attaches subordinate interfaces to the given timer interface.

        Parameters
        ----------
        subordinates: [InterpacketTimerInterface, Signal]
            Each :class:`InterpacketTimerInterface` is provided will be fully connected to a given
            timer interface. Each ``Signal`` provided will be interpreted as a timer reset, and added
            to the list of all resets.
        """

        start_conditions = []
        fragments = []

        for subordinate in subordinates:

            # If this is an interface, add its start to our list of start conditions,
            # and propagate our timer outputs to it.
            if isinstance(subordinate, self.__class__):
                start_conditions.append(subordinate.start)
                fragments.extend([
                    subordinate.tx_allowed.eq(self.tx_allowed),
                    subordinate.tx_timeout.eq(self.tx_timeout),
                    subordinate.rx_timeout.eq(self.rx_timeout)
                ])

            # If it's a signal, connect it directly as a start signal.
            else:
                start_conditions.append(subordinate)

        # Merge all of our start conditions into a single start condition, and
        # then add that to our fragment list.
        start_condition = functools.reduce(operator.__or__, start_conditions)
        fragments.append(self.start.eq(start_condition))

        return fragments


#
# Stream interfaces for USB packet handling
#

class USBInStreamInterface(Record):
    """ Variant of StreamInterface optimized for USB IN transmission.

    This stream interface is nearly identical to StreamInterface, with the following
    restriction: the `valid` signal _must_ be held high for every packet between `first`
    and `last`, inclusively.
    """

    def __init__(self):
        super().__init__([
            ('valid',   1),
            ('ready',   1),
            ('first',   1),
            ('last',    1),
            ('payload', 8),
        ])


    def bridge_to(self, utmi_tx):
        """ Generates a list of connections that connect this stream to the provided UTMITransmitInterface. """

        return [
            utmi_tx.valid.eq(self.valid),
            utmi_tx.data.eq(self.payload),
            self.ready.eq(utmi_tx.ready)
        ]



class USBOutStreamInterface(Record):
    """ Variant of StreamInterface optimized for USB OUT receipt.

    This is a heavily simplified version of our StreamInterface, which omits the 'first',
    'last', and 'ready' signals. Instead, the streamer indicates when data is valid using
    the 'next' signal; and the receiver must keep time.
    """

    def __init__(self, payload_width=8):
        super().__init__([
            ('valid',    1),
            ('next',     1),
            ('payload',  payload_width),
        ])


    def bridge_to(self, utmi_rx):
        """ Generates a list of connections that connect this stream to the provided UTMIReceiveInterface. """

        return [
            self.valid.eq(utmi_rx.rx_active),
            self.next.eq(utmi_rx.rx_valid),
            self.payload.eq(utmi_rx.rx_data)
        ]



#
# Gateware.
#

class USBTokenDetector(Module):
    """ Gateware that parses token packets and generates relevant events.

    Attributes
    ----------
    interface: TokenDetectorInterface
        The interface that contains token detection events, and information about detected tokens.
    speed: Signal(2), input
        Carries a ``USBSpeed`` constant identifying the device's current operating speed.
    address: Signal(7), input -or- output
        If :parameter:``filter_by_address`` is true, this is an input that filters our event detector so
        it only reports tokens directed at a given address.
        If ``filter_by_address`` is false, this is an output that contains the address of the most
        recent token.


    Parameters
    ----------
        utmi: UTMIInterface
            The UTMI bus to observe.
        filter_by_address: bool
            If true, this detector will only report events for the address supplied in the address[] field.
    """

    SOF_PID      = 0b0101
    TOKEN_SUFFIX =   0b01

    def __init__(self, *, utmi, filter_by_address=True, domain_clock=60e6, fs_only=False):
        self.utmi = utmi
        self.filter_by_address = filter_by_address
        self._domain_clock = domain_clock
        self._fs_only = fs_only

        #
        # I/O port
        #
        self.interface = TokenDetectorInterface()
        self.speed     = Signal(2)
        self.address   = Signal(7)

        # Internal signals
        token_data       = Signal(11)
        current_pid      = Signal(4)

        # FSM helper signals (must be defined before use in FSM)
        is_normal_token = Signal()
        is_ping_token = Signal()
        is_valid_pid = Signal()
        expected_crc = Signal(5)
        token_applicable = Signal()

        # Instantiate a dedicated inter-packet delay timer
        self.submodules.timer = USBInterpacketTimer(domain_clock=self._domain_clock, fs_only=self._fs_only)
        timer = InterpacketTimerInterface()
        self.comb += self.timer.speed.eq(self.speed)

        # Generate our 'ready_for_response' signal
        self.timer.add_interface(timer)
        self.comb += self.interface.ready_for_response.eq(timer.tx_allowed)

        # Generate our convenience status signals.
        self.comb += [
            self.interface.is_in.eq(self.interface.pid == USBPacketID.IN),
            self.interface.is_out.eq(self.interface.pid == USBPacketID.OUT),
            self.interface.is_setup.eq(self.interface.pid == USBPacketID.SETUP),
            self.interface.is_ping.eq(self.interface.pid == USBPacketID.PING)
        ]

        self.comb += token_applicable.eq(token_data[0:7] == self.address)

        # Main FSM
        fsm = FSM()
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # Keep our strobes un-asserted unless otherwise specified.
        self.sync.usb += [
            self.interface.new_frame.eq(0),
            self.interface.new_token.eq(0)
        ]

        # IDLE -- waiting for a packet to be presented
        fsm.act("IDLE",
            If(self.utmi.rx_active,
                NextState("READ_PID")
            )
        )

        # READ_PID -- read the packet's ID, and determine if it's a token.
        fsm.act("READ_PID",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                # Use direct comparisons instead of intermediate signals
                # to ensure proper evaluation within the same cycle
                # Note: ~ operator needs to be masked to 4 bits for proper comparison
                If(((self.utmi.rx_data[0:2] == self.TOKEN_SUFFIX) |
                    (self.utmi.rx_data[0:4] == USBPacketID.PING)) &
                   (self.utmi.rx_data[0:4] == (~self.utmi.rx_data[4:8] & 0b1111)),
                    NextValue(current_pid, self.utmi.rx_data),
                    NextState("READ_TOKEN_0")
                ).Else(
                    NextState("IRRELEVANT")
                )
            )
        )

        # READ_TOKEN_0
        fsm.act("READ_TOKEN_0",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                NextValue(token_data, self.utmi.rx_data),
                NextState("READ_TOKEN_1")
            )
        )

        # READ_TOKEN_1
        fsm.act("READ_TOKEN_1",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                expected_crc.eq(self._generate_crc_for_token(Cat(token_data[0:8], self.utmi.rx_data[0:3]))),

                # If the token has a valid CRC, capture it...
                If(self.utmi.rx_data[3:8] == expected_crc,
                    NextValue(token_data[8:], self.utmi.rx_data),
                    NextState("TOKEN_COMPLETE")
                ).Else(
                    NextState("IRRELEVANT")
                )
            )
        )

        # TOKEN_COMPLETE: we've received a full token; and now need to wait
        # for the packet to be complete.
        fsm.act("TOKEN_COMPLETE",
            If(~self.utmi.rx_active,
                NextState("IDLE"),

                # Special case: if this is a SOF PID, we'll extract
                # the frame number from this, rather than our typical
                # token fields.
                If(current_pid == self.SOF_PID,
                    NextValue(self.interface.frame, token_data),
                    NextValue(self.interface.new_frame, 1)
                ).Else(
                    # Otherwise, extract the address and endpoint from the token,
                    # and report the captured pid.

                    # If we're filtering by address, only count this token if it's releveant to our address.
                    # Otherwise, always count tokens -- we'll report the address on the output.
                    If(token_applicable if self.filter_by_address else 1,
                        NextValue(self.interface.pid, current_pid),
                        NextValue(self.interface.new_token, 1),
                        NextValue(Cat(self.interface.address, self.interface.endpoint), token_data),
                        # Start our interpacket-delay timer.
                        timer.start.eq(1)
                    ).Else(
                        # If we don't count the token, clear the state so we don't act on following packets.
                        NextValue(self.interface.pid, 0)
                    )
                )
            ).Elif(self.utmi.rx_valid,
                NextState("IRRELEVANT")
            )
        )

        # IRRELEVANT -- we've encountered a non-token packet; wait for it to end
        fsm.act("IRRELEVANT",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            )
        )

    @staticmethod
    def _generate_crc_for_token(token):
        """ Generates a 5-bit signal equivalent to the CRC check for the provided token packet. """

        def xor_bits(*indices):
            bits = (token[len(token) - 1 - i] for i in indices)
            return functools.reduce(operator.__xor__, bits)

        # Implements the CRC polynomial from the USB specification.
        return Cat(
             xor_bits(10, 9, 8, 5, 4, 2),
            ~xor_bits(10, 9, 8, 7, 4, 3, 1),
             xor_bits(10, 9, 8, 7, 6, 3, 2, 0),
             xor_bits(10, 7, 6, 4, 1),
             xor_bits(10, 9, 6, 5, 3, 0)
        )



class USBHandshakeDetector(Module):
    """ Gateware that detects handshake packets.

    Attributes
    -----------
    detected: HandshakeExchangeInterface
        Strobes that indicate which handshakes we're detecting.

    Parameters
    ----------
    utmi: [UTMIInterface, UTMITranslator]
        The UTMI interface to listen on.
    """

    ACK_PID   = 0b0010
    NAK_PID   = 0b1010
    STALL_PID = 0b1110
    NYET_PID  = 0b0110

    def __init__(self, *, utmi):
        self.utmi = utmi

        #
        # I/O port
        #
        self.detected = HandshakeExchangeInterface(is_detector=True)

        active_pid = Signal(4)

        # Keep our strobes un-asserted unless otherwise specified.
        self.sync.usb += [
            self.detected.ack.eq(0),
            self.detected.nak.eq(0),
            self.detected.stall.eq(0),
            self.detected.nyet.eq(0),
        ]

        fsm = FSM()
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # IDLE -- waiting for a packet to be presented
        fsm.act("IDLE",
            If(self.utmi.rx_active,
                NextState("READ_PID")
            )
        )

        # READ_PID -- read the packet's ID.
        fsm.act("READ_PID",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                # If we have a valid PID, move to capture it.
                # Note: ~ operator needs to be masked to 4 bits for proper comparison
                If(self.utmi.rx_data[0:4] == (~self.utmi.rx_data[4:8] & 0b1111),
                    NextValue(active_pid, self.utmi.rx_data),
                    NextState("AWAIT_COMPLETION")
                ).Else(
                    NextState("IRRELEVANT")
                )
            )
        )

        # TOKEN_COMPLETE: we've received a full token; and now need to wait
        # for the packet to be complete.
        fsm.act("AWAIT_COMPLETION",
            If(~self.utmi.rx_active,
                NextValue(self.detected.ack, active_pid == self.ACK_PID),
                NextValue(self.detected.nak, active_pid == self.NAK_PID),
                NextValue(self.detected.stall, active_pid == self.STALL_PID),
                NextValue(self.detected.nyet, active_pid == self.NYET_PID),
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                NextState("IRRELEVANT")
            )
        )

        # IRRELEVANT -- we've encountered a malformed or non-handshake packet
        fsm.act("IRRELEVANT",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            )
        )


class USBDataPacketCRC(Module):
    """ Gateware that computes a running CRC-16.

    By default, this module has no connections to the modules that use it.

    These are added using :attr:`add_interface`; this module supports an arbitrary
    number of connection interfaces; see :attr:`add_interface()` for restrictions.

    Attributes
    ----------
    rx_data: Signal(8), input
        Receive data input; can be carried directly from a UTMI interface.
    rx_valid: Signal(), input
        Receive validity signal; can be carried directly from a UTMI interface.

    tx_data: Signal(8), input
        Transmit data input; can be carried directly from a UTMI interface.
    tx_valid: Signal(), input
        When high, the `tx_data` input is used to update the CRC.

    Parameters
    ----------
    initial_value: [int, Const]
            The initial value of the CRC shift register; the USB default is used if not provided.
    """

    def __init__(self, initial_value=0xFFFF):

        self._initial_value = initial_value

        # List of interfaces to work with.
        # This list is populated dynamically by calling .add_interface().
        self._interfaces    = []

        #
        # I/O port
        #
        self.clear = Signal()

        self.rx_data  = Signal(8)
        self.rx_valid = Signal()

        self.tx_data  = Signal(8)
        self.tx_valid = Signal()

        self.crc   = Signal(16, reset=initial_value)

        # Register that contains the running CRCs.
        self._crc        = Signal(16, reset=self._initial_value)

        # Signal that contains the output version of our active CRC.
        self._output_crc = Signal(16)

        # Internal clear signal - will be connected to interface.start signals in do_finalize()
        self._clear_internal = Signal()

        # If we're clearing our CRC in progress, move our holding register back to
        # our initial value.
        self.sync.usb += [
            If(self._clear_internal,
                self._crc.eq(self._initial_value)
            ).Elif(self.rx_valid,
                self._crc.eq(self._generate_next_crc(self._crc, self.rx_data))
            ).Elif(self.tx_valid,
                self._crc.eq(self._generate_next_crc(self._crc, self.tx_data))
            )
        ]

        # Convert from our intermediary "running CRC" format into the current CRC-16...
        # In migen, we use bit slicing to reverse: crc[::-1] reverses the bits
        self.comb += self._output_crc.eq(~Cat(self._crc[i] for i in range(15, -1, -1)))

        # Connect the public crc signal to the output
        self.comb += self.crc.eq(self._output_crc)

    def do_finalize(self):
        # Called after all interfaces have been added via add_interface()
        # Now we can safely connect the start signals

        # We'll clear our CRC whenever any of our interfaces request it.
        if self._interfaces:
            start_signals = [interface.start for interface in self._interfaces]
            self.comb += self._clear_internal.eq(functools.reduce(operator.__or__, start_signals))
        else:
            self.comb += self._clear_internal.eq(0)

        # ... and connect it to each of our interfaces.
        for interface in self._interfaces:
            self.comb += interface.crc.eq(self._output_crc)


    def add_interface(self, interface):
        """ Adds an interface to the CRC generator module.

        Each interface can reset the CRC; and can read the current CRC value.
        No arbitration is performed; it's assumed that no more than one interface
        will be computing a running CRC at at time.

        Parameters
        ----------
        interface: DataCRCInterface
            The interface to be added; accepts control signals from other modules, and
            brings CRC output to them. This method can be called multiple times to generate
            multiplpe CRCs.
        """
        self._interfaces.append(interface)


    def _generate_next_crc(self, current_crc, data_in):
        """ Generates the next round of a bytewise USB CRC16. """
        xor_reduce = lambda bits : functools.reduce(operator.__xor__, bits)

        # Extracted from the USB spec's definition of the CRC16 polynomial.
        return Cat(
            xor_reduce(data_in)      ^ xor_reduce(current_crc[ 8:16]),
            xor_reduce(data_in[0:7]) ^ xor_reduce(current_crc[ 9:16]),
            xor_reduce(data_in[6:8]) ^ xor_reduce(current_crc[ 8:10]),
            xor_reduce(data_in[5:7]) ^ xor_reduce(current_crc[ 9:11]),
            xor_reduce(data_in[4:6]) ^ xor_reduce(current_crc[10:12]),
            xor_reduce(data_in[3:5]) ^ xor_reduce(current_crc[11:13]),
            xor_reduce(data_in[2:4]) ^ xor_reduce(current_crc[12:14]),
            xor_reduce(data_in[1:3]) ^ xor_reduce(current_crc[13:15]),

            xor_reduce(data_in[0:2]) ^ xor_reduce(current_crc[14:16]) ^ current_crc[0],
            data_in[0] ^ current_crc[1] ^ current_crc[15],
            current_crc[2],
            current_crc[3],
            current_crc[4],
            current_crc[5],
            current_crc[6],
            xor_reduce(data_in) ^ xor_reduce(current_crc[7:16]),
        )


class USBDataPacketReceiver(Module):
    """ Gateware that converts received USB data packets into a data-stream packets.

    It's important to note that packet payloads are mostly directly carried over from UTMI.
    Since USB data is received -prior- to its CRC, one cannot know if a packet is valid until
    after it has been compeltely received. As a result, this interface will generate data of
    unknown validity, followed by a strobe on either :attr:`packet_complete` or :attr:`crc_mismatch`.
    The receiving interface must be prepared to handle :attr:`crc_mismatch` by discarding the received
    data.


    Attributes
    ----------
    data_crc: DataCRCInterface
        Connection to the CRC generator.
    timer: InterpacketTimerInterface
        Connection to our interpacket timer.
    stream: USBOutDataStream, output
        Stream that carries captured packet data.

    active_pid: Signal(4), output
        The PID of the data currently being received.
    packet_id: Signal(4), output
        The packet ID of the most recently captured PID. Becomes valid simultaneous to a strobe on
        :attr:`packet_complete` or :attr:`crc_mismatch`.

    packet_complete: Signal(), output
        Strobe that pulses high when a new packet is delivered with a valid CRC.
    crc_mismatch: Signal(), output
        Strobe that pulses high when the given packet has a CRC mismatch; and thus the data
        received this far should be discarded.
    ready_for_response: Signal(), output
        Strobe that indicates that an inter-packet delay has passed since :attr:`packet_complete`,
        and thus we're now ready to respond with a handshake.

    Parameters
    ----------
    utmi: UTMIInterface, or equivalent
        The UTMI bus to observe.
    max_packet_size: int
        The maximum packet (payload) size to be deserialized, in bytes.

    standalone: bool
        Debug value. If True, a submodule CRC generator will be created.
    speed: USBSpeed
        USBSpeed signal or constant that specifies our speed in standalone mode.
    """

    _DATA_SUFFIX = 0b11

    def __init__(self, *, utmi, standalone=False, speed=None):

        self.utmi        = utmi
        self.standalone  = standalone
        self.speed       = speed

        #
        # I/O port
        #
        self.data_crc           = DataCRCInterface()
        self.timer              = InterpacketTimerInterface()
        self.stream             = USBOutStreamInterface()

        self.active_pid         = Signal(4)

        self.packet_complete    = Signal()
        self.ready_for_response = Signal()
        self.crc_mismatch       = Signal()
        self.packet_id          = Signal(4)

        # If we're in standalone mode, create our dependencies for us.
        if self.standalone:
            self.submodules.crc = crc = USBDataPacketCRC()
            crc.add_interface(self.data_crc)

            self.submodules.interpacket_timer = interpacket_timer = USBInterpacketTimer()
            interpacket_timer.add_interface(self.timer)

            if not self.speed:
                self.speed = USBSpeed.FULL

            self.comb += [
                # Connect our CRC generator...
                crc.rx_data.eq(self.utmi.rx_data),
                crc.rx_valid.eq(self.utmi.rx_valid),
                crc.tx_valid.eq(0),

                # ... and our timer.
                interpacket_timer.speed.eq(self.speed)
            ]


        # CRC-16 tracking signals.
        last_byte_crc = Signal(16)
        last_word_crc = Signal(16)

        # Keeps track of the most recently received word; for CRC comparison/removal.
        data_pipeline     = Signal(16)

        # Keep our control signals + strobes un-asserted unless otherwise specified.
        self.sync.usb += [
            self.packet_complete.eq(0),
            self.crc_mismatch.eq(0),
        ]
        self.comb += [
            self.stream.next.eq(0),
            self.data_crc.start.eq(0),
        ]


        fsm = FSM()
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # IDLE -- waiting for a packet to be presented
        fsm.act("IDLE",
            If(self.utmi.rx_active,
                NextState("READ_PID")
            )
        )

        # READ_PID -- read the packet's ID.
        fsm.act("READ_PID",
            # Clear our CRC; as we're potentially about to start a new packet.
            self.data_crc.start.eq(1),

            If(~self.utmi.rx_active,
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                # If this is a data packet, capture its PID.
                # Note: ~ operator needs to be masked to 4 bits for proper comparison
                If((self.utmi.rx_data[0:4] == (~self.utmi.rx_data[4:8] & 0b1111)) & (self.utmi.rx_data[0:2] == self._DATA_SUFFIX),
                    NextValue(self.active_pid, self.utmi.rx_data),
                    NextState("RECEIVE_FIRST_BYTE")
                ).Else(
                    NextState("IRRELEVANT")
                )
            )
        )


        # RECEIVE_FIRST_BYTE -- capture the first byte into our pipeline.
        # We'll always pipeline two bytes before we start emitting; as we won't want to
        # pass through the last two bytes (the CRC).
        fsm.act("RECEIVE_FIRST_BYTE",
            If(self.utmi.rx_valid,
                NextValue(data_pipeline[8:], self.utmi.rx_data),
                NextValue(last_byte_crc, self.data_crc.crc),
                NextState("RECEIVE_SECOND_BYTE")
            ),

            # If our packet stops before we see the first to bytes, we'll return to idle.
            # There's nothing to clean up, as we've never touched the stream.
            If(~self.utmi.rx_active,
                NextState("IDLE")
            )
        )


        # RECEIVE_SECOND_BYTE-- capture the second byte into our pipeline.
        fsm.act("RECEIVE_SECOND_BYTE",
            If(self.utmi.rx_valid,
                NextValue(data_pipeline[8:], self.utmi.rx_data),
                NextValue(data_pipeline[0:8], data_pipeline[8:]),
                NextValue(last_byte_crc, self.data_crc.crc),
                NextValue(last_word_crc, last_byte_crc),
                NextState("RECEIVE_AND_EMIT")
            ).Elif(~self.utmi.rx_active,
                NextState("IDLE")
            )
        )


        # RECEIVE_AND_EMIT -- receive bytes into our pipeline, and emit them.
        # Now that we have more than two bytes captured, we can start emitting bytes.
        # We'll always be emitting bytes that are two old -- so we can stop before our CRC.:
        fsm.act("RECEIVE_AND_EMIT",
            self.stream.valid.eq(1),

            If(self.utmi.rx_valid,
                self.stream.payload.eq(data_pipeline[0:8]),
                self.stream.next.eq(1),

                NextValue(data_pipeline[8:], self.utmi.rx_data),
                NextValue(data_pipeline[0:8], data_pipeline[8:]),
                NextValue(last_byte_crc, self.data_crc.crc),
                NextValue(last_word_crc, last_byte_crc),
            ),

            # Once we stop receiving data, check our CRC and finish.
            If(~self.utmi.rx_active,
                # If our CRC matches, this is a valid packet!
                If(last_word_crc == data_pipeline,
                    # Indicate so...
                    NextValue(self.packet_id, self.active_pid),
                    NextValue(self.packet_complete, 1),
                    # ... start counting our interpacket delay...
                    self.timer.start.eq(1),
                    # ... and wait for it to complete.
                    NextState("INTERPACKET_DELAY")
                ).Else(
                    # Otherwise, flag this as a CRC mismatch.
                    NextValue(self.crc_mismatch, 1),
                    # ... and return to IDLE.
                    NextState("IDLE")
                )
            )
        )


        # INTERPACKET_DELAY -- we've received a valid packet; wait for an
        # interpacket delay before moving back to IDLE.
        fsm.act("INTERPACKET_DELAY",
            If(self.timer.tx_allowed,
                self.ready_for_response.eq(1),
                NextState("IDLE")
            )
        )


        # IRRELEVANT -- we've encountered a malformed or non-DATA packet.
        fsm.act("IRRELEVANT",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            )
        )


class USBDataPacketDeserializer(Module):
    """ Gateware that captures USB data packet contents and parallelizes them.

    Attributes
    ----------
    data_crc: DataCRCInterface
        Connection to the CRC generator.

    new_packet: Signal(), output
        Strobe that pulses high for a single cycle when a new packet is delivered.
    packet_id: Signal(4), output
        The packet ID of the captured PID.

    packet: Signal(max_packet_size), output
        Packet data for a the most recently received packet.
    length: Signal(range(0, max_packet_length +1)), output
        The length of the packet data presented on the packet[] output.

    Parameters
    ----------
    utmi: UTMIInterface, or equivalent
        The UTMI bus to observe.
    max_packet_size: int
        The maximum packet (payload) size to be deserialized, in bytes.
    create_crc_generator: bool
        If True, a submodule CRC generator will be created. Excellent for testing.
    """

    _DATA_SUFFIX = 0b11

    def __init__(self, *, utmi, max_packet_size=64, create_crc_generator=False):

        self.utmi                 = utmi
        self._max_packet_size     = max_packet_size
        self.create_crc_generator = create_crc_generator

        #
        # I/O port
        #
        self.data_crc    = DataCRCInterface()

        self.new_packet  = Signal()

        self.packet_id   = Signal(4)
        self.packet      = Array(Signal(8, name=f"packet_{i}") for i in range(max_packet_size))
        self.length      = Signal(max=max_packet_size + 1)

        max_size_with_crc = self._max_packet_size + 2

        # If we're creating an internal CRC generator, create a submodule
        # and hook it up.
        if self.create_crc_generator:
            self.submodules.crc = crc = USBDataPacketCRC()
            crc.add_interface(self.data_crc)

            self.comb += [
                crc.rx_data.eq(self.utmi.rx_data),
                crc.rx_valid.eq(self.utmi.rx_valid),
                crc.tx_valid.eq(0)
            ]

        # CRC-16 tracking signals.
        last_byte_crc = Signal(16)
        last_word_crc = Signal(16)

        # Currently captured PID.
        active_pid         = Signal(4)

        # Active packet transfer.
        active_packet      = Array(Signal(8) for _ in range(max_size_with_crc))
        position_in_packet = Signal(max=max_size_with_crc)

        # Keeps track of the most recently received word; for CRC comparison.
        last_word          = Signal(16)

        # FSM helper signals
        is_data = Signal()
        is_valid_pid = Signal()

        # Keep our control signals + strobes un-asserted unless otherwise specified.
        self.sync.usb += self.new_packet.eq(0)
        self.comb += self.data_crc.start.eq(0)

        fsm = FSM()
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # IDLE -- waiting for a packet to be presented
        fsm.act("IDLE",
            If(self.utmi.rx_active,
                NextState("READ_PID")
            )
        )

        # READ_PID -- read the packet's ID.
        fsm.act("READ_PID",
            # Clear our CRC; as we're potentially about to start a new packet.
            self.data_crc.start.eq(1),

            If(~self.utmi.rx_active,
                NextState("IDLE")
            ).Elif(self.utmi.rx_valid,
                # Use direct comparisons instead of intermediate signals
                # Note: ~ operator needs to be masked to 4 bits for proper comparison
                If((self.utmi.rx_data[0:4] == (~self.utmi.rx_data[4:8] & 0b1111)) &
                   (self.utmi.rx_data[0:2] == self._DATA_SUFFIX),
                    NextValue(active_pid, self.utmi.rx_data),
                    NextValue(position_in_packet, 0),
                    NextState("CAPTURE_DATA")
                ).Else(
                    NextState("IRRELEVANT")
                )
            )
        )


        fsm.act("CAPTURE_DATA",
            # If we have a new byte of data, capture it.
            If(self.utmi.rx_valid,
                # If this would over-fill our internal buffer, fail out.
                If(position_in_packet >= max_size_with_crc,
                    # TODO: potentially signal the babble?
                    NextState("IRRELEVANT")
                ).Else(
                    # Use NextValue for synchronous assignment to capture data
                    NextValue(active_packet[position_in_packet], self.utmi.rx_data),
                    NextValue(position_in_packet, position_in_packet + 1),
                    NextValue(last_word, Cat(last_word[8:], self.utmi.rx_data)),
                    NextValue(last_word_crc, last_byte_crc),
                    NextValue(last_byte_crc, self.data_crc.crc),
                )
            ),

            # If this is the end of our packet, validate our CRC and finish.
            If(~self.utmi.rx_active,
                If(last_word_crc == last_word,
                    NextValue(self.packet_id, active_pid),
                    NextValue(self.length, position_in_packet - 2),
                    NextValue(self.new_packet, 1),
                    # Use NextValue for synchronous assignment to capture packet data
                    [NextValue(self.packet[i], active_packet[i]) for i in range(self._max_packet_size)],
                    NextState("IDLE")
                )
            )
        )

        # IRRELEVANT -- we've encountered a malformed or non-handshake packet
        fsm.act("IRRELEVANT",
            If(~self.utmi.rx_active,
                NextState("IDLE")
            )
        )


class USBDataPacketGenerator(Module):
    """ Module that converts a FIFO-style stream into a USB data packet.

    Handles steps such as PID generation and CRC-16 injection.

    As a special case, if the stream pulses `last` (with valid=1) without pulsing
    `first`, we'll send a zero-length packet.

    Attributes
    ----------

    data_pid: Signal(2), input
        The data packet number to use. The potential PIDS are: 0 = DATA0, 1 = DATA1,
        2 = DATA2, 3 = MDATA; the interface is designed so that most endpoints can tie the MSb to
        zero and then perform PID toggling by toggling the LSb.

    crc: DataCRCInterface
        Interface to our data CRC generator.
    stream: USBInStreamInterface
        Stream input for the raw data to be transmitted.
    tx: UTMITransmitInterface
        UTMI-subset transmit interface

    Parameters
    ----------
    standalone: bool
        If True, this unit will include its internal CRC generator. Perfect for unit testing or debugging.
    """

    def __init__(self, standalone=False):

        self.standalone = standalone

        #
        # I/O port
        #
        self.data_pid     = Signal(2)

        self.crc          = DataCRCInterface()
        self.stream       = USBInStreamInterface()
        self.tx           = UTMITransmitInterface()

        # Create a mux that maps our data_pid value to our actual data PID.
        data_pids = [
            Constant(0xC3, 8), # DATA0
            Constant(0x4B, 8), # DATA1
            Constant(0x87, 8), # DATA2
            Constant(0x0F, 8)  # DATAM
        ]

        # Stores the current data pid; latched in at the start of a transmission.
        current_data_pid = Signal(8)

        # Register that stores the final CRC byte.
        # Capturing this before the end of the packet ensures we can still send
        # the correct final CRC byte; even if the CRC generator updates its computation
        # when the first byte of the CRC is transmitted.
        remaining_crc = Signal(8)

        # Flag that stores whether we're sending a zero-length packet.
        is_zlp = Signal()

        # If we're creating an internal CRC generator, create a submodule
        # and hook it up.
        if self.standalone:
            self.submodules.crc_gen = crc_gen = USBDataPacketCRC()
            crc_gen.add_interface(self.crc)

            self.comb += [
                crc_gen.rx_valid.eq(0),
                crc_gen.tx_data.eq(self.stream.payload),
                crc_gen.tx_valid.eq(self.tx.ready)
            ]

        fsm = FSM()
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # IDLE -- waiting for an active transmission to start.
        fsm.act("IDLE",
            # We won't consume any data while we're in the IDLE state.
            self.stream.ready.eq(0),
            # Latch in the requested data PID.
            NextValue(current_data_pid, Array(data_pids)[self.data_pid]),

            # Once a packet starts, we'll need to transmit the data PID.
            If(self.stream.first & self.stream.valid,
                NextValue(is_zlp, 0),
                NextState("SEND_PID")
            ).Elif(self.stream.last & self.stream.valid,
                # Special case: if `last` pulses without first, we'll consider this
                # a zero-length packet ("a packet without a first byte").
                NextValue(is_zlp, 1),
                NextState("SEND_PID")
            )
        )


        # SEND_PID -- prepare for the transaction by sending the data packet ID.
        fsm.act("SEND_PID",
            # Prepare for a new payload by starting a new CRC calculation.
            self.crc.start.eq(1),
            # Send the USB packet ID for our data packet...
            self.tx.data.eq(current_data_pid),
            self.tx.valid.eq(1),
            # ... and don't consume any data.
            self.stream.ready.eq(0),

            # Advance once the PHY accepts our PID.
            If(self.tx.ready,
                # If this is a ZLP, we don't have a payload to send.
                # Skip directly to sending our CRC.
                If(is_zlp,
                    NextState("SEND_CRC_FIRST")
                ).Else(
                    # Otherwise, we have a payload. Send it.
                    NextState("SEND_PAYLOAD")
                )
            )
        )


        # SEND_PAYLOAD -- send the data payload for our stream
        fsm.act("SEND_PAYLOAD",
            # While sending the payload, we'll essentially connect
            # our stream directly through to the ULPI transmitter.
            self.stream.bridge_to(self.tx),

            # We'll stop sending once the packet ends, and move on to our CRC.
            If(self.tx.ready & (self.stream.last | ~self.stream.valid),
                NextState("SEND_CRC_FIRST")
            )
        )


        # SEND_CRC_FIRST -- send the first byte of the packet's CRC
        fsm.act("SEND_CRC_FIRST",
            # Capture the current CRC for use in the next byte...
            NextValue(remaining_crc, self.crc.crc[8:]),
            # Send the relevant CRC byte...
            self.tx.data.eq(self.crc.crc[0:8]),
            self.tx.valid.eq(1),

            # ... and move on to the next one.
            If(self.tx.ready,
                NextState("SEND_CRC_SECOND")
            )
        )


        # SEND_CRC_LAST -- send the last byte of the packet's CRC
        fsm.act("SEND_CRC_SECOND",
            # Send the relevant CRC byte...
            self.tx.data.eq(remaining_crc),
            self.tx.valid.eq(1),

            # ... and return to idle.
            If(self.tx.ready,
                NextState("IDLE")
            )
        )



class USBHandshakeGenerator(Module):
    """ Module that generates handshake packets, on request.

    Attributes:

    issue_ack: Signal(), input
        Pulsed to generate an ACK handshake packet.
    issue_nak: Signal(), input
        Pulsed to generate a NAK handshake packet.
    issue_stall: Signal(), input
        Pulsed to generate a STALL handshake.

    tx: UTMITransmitInterface
        Interface to the relevant UTMI interface.
    """

    # Full contents of an ACK, NAK, and STALL packet.
    # These include the four check bits; which consist of the inverted PID.
    _PACKET_ACK   = 0b11010010
    _PACKET_NAK   = 0b01011010
    _PACKET_STALL = 0b00011110

    def __init__(self):

        #
        # I/O port
        #
        self.issue_ack    = Signal()
        self.issue_nak    = Signal()
        self.issue_stall  = Signal()

        self.tx           = UTMITransmitInterface()

        fsm = FSM()
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # IDLE -- we haven't yet received a request to transmit
        fsm.act("IDLE",
            self.tx.valid.eq(0),

            # Wait until we have an ACK, NAK, or STALL request;
            # Then set our data value to the appropriate PID,
            # in preparation for the next cycle.
            # Independent Ifs, matching LUNA: last match wins (STALL > NAK > ACK).
            If(self.issue_ack,
                NextValue(self.tx.data, self._PACKET_ACK),
                NextState("TRANSMIT")
            ),
            If(self.issue_nak,
                NextValue(self.tx.data, self._PACKET_NAK),
                NextState("TRANSMIT")
            ),
            If(self.issue_stall,
                NextValue(self.tx.data, self._PACKET_STALL),
                NextState("TRANSMIT")
            )
        )


        # TRANSMIT -- send the handshake.
        fsm.act("TRANSMIT",
            self.tx.valid.eq(1),

            # Once we know the transmission will be accepted, we're done!
            # Move back to IDLE.
            If(self.tx.ready,
                NextState("IDLE")
            )
        )


class USBInterpacketTimer(Module):
    """ Module that tracks inter-packet timings, enforcing spec-mandated packet gaps.

    Ports other than :attr:`speed` are added dynamically via :method:add_interface`.

    Attributes
    ----------
    speed: Signal(2), input
        The device's current operating speed. Should be a USBSpeed enumeration value --
        0 for high, 1 for full, 2 for low.

    """

    # Per the USB 2.0 and ULPI 1.1 specifications, after receipt:
    #   - A FS/LS device needs to wait 2 bit periods before transmitting; and must
    #     respond before 6.5 bit times pass. [USB2, 7.1.18.1]
    #   - Two FS bit periods is equivalent to 10 ULPI clocks, and two LS periods is
    #     equivalent to 80 ULPI clocks. 6.5 FS bit periods is equivalent to 32 ULPI clocks,
    #     and 6.5 LS bit periods is equivalent to 260 ULPI clocks. [ULPI 1.1, Figure 18].
    #   - A HS device needs to wait 8 HS bit periods before transmitting [USB2, 7.1.18.2].
    #     Each ULPI cycle is 8 HS bit periods, so we'll only need to wait one cycle.
    _HS_RX_TO_TX_DELAY     = {60e6: (  1,  24)}
    _FS_RX_TO_TX_DELAY     = {60e6: ( 10,  32), 12e6: (2, 7)}
    _LS_RX_TO_TX_DELAY     = {60e6: ( 80, 260)}

    # Per the USB 2.0 and ULPI 1.1 specifications, after transission:
    #   - A FS/LS can assume it won't receive a response after 16 bit times [USB2, 7.1.18.1].
    #     This is equivalent to 80 ULPI clocks (FS), or 640 ULPI clocks (LS).
    #   - A HS device can assume it won't receive a response after 736 bit times.
    #     This is equivalent to 92 ULPI clocks.
    _HS_TX_TO_RX_TIMEOUT = {60e6:  92}
    _FS_TX_TO_RX_TIMEOUT = {60e6:  80, 12e6: 16}
    _LS_TX_TO_RX_TIMEOUT = {60e6: 640}


    def __init__(self, domain_clock=60e6, fs_only=False):
        self._fs_only = fs_only

        # Start off with empty delays -- this doesn't change anything, but makes
        # linters happy. :)
        self._hs_rx_to_tx_delay   = None
        self._ls_rx_to_tx_delay   = None
        self._hs_rx_to_tx_timeout = None
        self._ls_rx_to_tx_timeout = None

        # Validate that we have a usable FS Rx/Tx delay.
        if domain_clock not in self._FS_RX_TO_TX_DELAY:
            raise ValueError(f"Domain clock must be in {self._FS_TX_TO_RX_TIMEOUT.keys()}, not {domain_clock}.")

        # Capture our FS delay for the current clock speed.
        self._fs_rx_to_tx_delay   = self._FS_RX_TO_TX_DELAY[domain_clock]
        self._fs_tx_to_rx_timeout = self._FS_TX_TO_RX_TIMEOUT[domain_clock]
        self._counter_max = self._FS_TX_TO_RX_TIMEOUT[domain_clock]

        # If we're not in a FS-only configuration, capture our other delays.
        if not self._fs_only:
            if domain_clock not in self._HS_RX_TO_TX_DELAY:
                raise ValueError(f"Domain clock must be in {self._FS_TX_TO_RX_TIMEOUT.keys()}, not {domain_clock}.")

            # Capute our HS and LS delays for the given clock speed.
            self._hs_rx_to_tx_delay   = self._HS_RX_TO_TX_DELAY[domain_clock]
            self._ls_rx_to_tx_delay   = self._LS_RX_TO_TX_DELAY[domain_clock]
            self._hs_tx_to_rx_timeout = self._HS_TX_TO_RX_TIMEOUT[domain_clock]
            self._ls_tx_to_rx_timeout = self._LS_TX_TO_RX_TIMEOUT[domain_clock]
            self._counter_max         = self._LS_TX_TO_RX_TIMEOUT[domain_clock]



        # List of interfaces to users of this module.
        self._interfaces               = []

        #
        # I/O port
        #
        self.speed = Signal(2)

        # Internal signals representing each of our timeouts.
        self._rx_to_tx_at_min  = Signal()
        self._rx_to_tx_at_max  = Signal()
        self._tx_to_rx_timeout = Signal()

        # Create a counter that will track our interpacket delays.
        # This should be able to count up to our longest delay. We'll allow our
        # counter to be able to increment one past its maximum, and let it saturate
        # there, after the count.
        counter = Signal(max=self._counter_max + 2)

        # Reset our timer whenever any of our interfaces request a timer start.
        # Note: self._interfaces is empty here; _any_reset is connected in do_finalize()
        self._any_reset = Signal()

        # When a reset is requested, start the counter from 0.
        self.sync.usb += [
            If(self._any_reset,
                counter.eq(0)
            ).Elif(counter < self._counter_max + 1,
                counter.eq(counter + 1)
            )
        ]

        #
        # Create our counter-progress strobes.
        # This could be made less repetitive, but spreading it out here
        # makes the documentation above clearer.
        #
        self.comb += [
            If(self.speed == USBSpeed.HIGH,
                self._rx_to_tx_at_min.eq(counter == self._hs_rx_to_tx_delay[0]) if not self._fs_only else self._rx_to_tx_at_min.eq(0),
                self._rx_to_tx_at_max.eq(counter == self._hs_rx_to_tx_delay[1]) if not self._fs_only else self._rx_to_tx_at_max.eq(0),
                self._tx_to_rx_timeout.eq(counter == self._hs_tx_to_rx_timeout) if not self._fs_only else self._tx_to_rx_timeout.eq(0)
            ).Elif(self.speed == USBSpeed.FULL,
                self._rx_to_tx_at_min.eq(counter == self._fs_rx_to_tx_delay[0]),
                self._rx_to_tx_at_max.eq(counter == self._fs_rx_to_tx_delay[1]),
                self._tx_to_rx_timeout.eq(counter == self._fs_tx_to_rx_timeout)
            ).Else(
                self._rx_to_tx_at_min.eq(counter == self._ls_rx_to_tx_delay[0]) if not self._fs_only else self._rx_to_tx_at_min.eq(0),
                self._rx_to_tx_at_max.eq(counter == self._ls_rx_to_tx_delay[1]) if not self._fs_only else self._rx_to_tx_at_max.eq(0),
                self._tx_to_rx_timeout.eq(counter == self._ls_tx_to_rx_timeout) if not self._fs_only else self._tx_to_rx_timeout.eq(0)
            )
        ]


    def add_interface(self, interface):
        """ Adds a connection to a user of this module.

        This module performs no multiplexing; it's assumed only one interface will be active at a time.

        Parameters
        ---------
        interface: InterpacketTimerInterface
            The interface to add.
        """
        self._interfaces.append(interface)

    def do_finalize(self):
        # Connect _any_reset and interface strobes.
        # This must be done in do_finalize() because interfaces are added
        # after __init__() via add_interface().
        if self._interfaces:
            reset_signals = [interface.start for interface in self._interfaces]
            self.comb += self._any_reset.eq(functools.reduce(operator.__or__, reset_signals))
        else:
            self.comb += self._any_reset.eq(0)

        # Tie our strobes to each of our consumers.
        for interface in self._interfaces:
            self.comb += [
                interface.tx_allowed.eq(self._rx_to_tx_at_min),
                interface.tx_timeout.eq(self._rx_to_tx_at_max),
                interface.rx_timeout.eq(self._tx_to_rx_timeout)
            ]
