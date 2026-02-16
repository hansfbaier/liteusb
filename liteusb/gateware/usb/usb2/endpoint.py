#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Gateware for working with abstract endpoints. """

import functools
import operator

from migen import *
from migen.genlib.record import Record

from .packet          import DataCRCInterface, InterpacketTimerInterface, TokenDetectorInterface
from .packet          import HandshakeExchangeInterface
from ..stream         import USBInStreamInterface, USBOutStreamInterface
from ...utils.bus     import OneHotMultiplexer


class EndpointInterface:
    """ Interface that connects a USB endpoint module to a USB device.

    Many non-control endpoints won't need to use the latter half of this structure;
    it will be automatically removed by the relevant synthesis tool.

    Attributes
    ----------
    tokenizer: TokenDetectorInterface, to detector
        Interface to our TokenDetector; notifies us of USB tokens.

    rx: USBOutStreamInterface, input stream to endpoint
        Receive interface for this endpoint.
    rx_complete: Signal(), input to endpoint
        Strobe that indicates that the concluding rx-stream was valid (CRC check passed).
    rx_ready_for_response: Signal(), input to endpoint
        Strobe that indicates that we're ready to respond to a complete transmission.
        Indicates that an interpacket delay has passed after an `rx_complete` strobe.
    rx_invalid: Signal(), input to endpoint
        Strobe that indicates that the concluding rx-stream was invalid (CRC check failed).
    rx_pid_toggle: Signal(), input to endpoint
        Value for the data PID toggle; 0 indicates we're receiving a DATA0; 1 indicates Data1.

    tx: USBInStreamInterface, output stream from endpoint
        Transmit interface for this endpoint.
    tx_pid_toggle: Signal(2), output from endpoint
        Value for the data PID toggle; 0 indicates we'll send DATA0; 1 indicates DATA1.
        2 indicates we'll send DATA2, while 3 indicates we'll send DATAM.

    handshakes_in: HandshakeExchangeInterface, input to endpoint
        Carries handshakes detected from the host.
    handshakes_out: HandshakeExchangeInterface, output from endpoint
        Carries handshakes generate by this endpoint.

    speed: Signal(2), input to endpoint
        The device's current operating speed. Should be a USBSpeed enumeration value --
        0 for high, 1 for full, 2 for low.

    active_address: Signal(7), input to endpoint
        Contains the device's current address.
    address_changed: Signal(), output from endpoint.
        Strobe; pulses high when the device's address should be changed.
    new_address: Signal(7), output from endpoint
        When :attr:`address_changed` is high, this field contains the address that should be adopted.

    active_config: Signal(8), input to endpoint
        The configuration number of the active configuration.
    config_changed: Signal(), output from endpoint
        Strobe; pulses high when the device's configuration should be changed.
    new_config: Signal(8)
        When `config_changed` is high, this field contains the configuration that should be applied.

    timer: InterpacketTimerInterface
        Interface to our interpacket timer.
    data_crc: DataCRCInterface
        Control connection for our data-CRC unit.
    """

    def __init__(self):
        self.data_crc              = DataCRCInterface()
        self.tokenizer             = TokenDetectorInterface()
        self.timer                 = InterpacketTimerInterface()

        self.speed                 = Signal(2)

        self.active_address        = Signal(7)
        self.address_changed       = Signal()
        self.new_address           = Signal(7)

        self.active_config         = Signal(8)
        self.config_changed        = Signal()
        self.new_config            = Signal(8)

        self.clear_endpoint_halt_out = Record([
            ('enable',    1),
            ('direction', 1),
            ('number',    4),
        ])
        self.clear_endpoint_halt_in  = Record([
            ('enable',    1),
            ('direction', 1),
            ('number',    4),
        ])

        self.rx                    = USBOutStreamInterface()
        self.rx_complete           = Signal()
        self.rx_ready_for_response = Signal()
        self.rx_invalid            = Signal()
        self.rx_pid_toggle         = Signal(2)

        self.tx                    = USBInStreamInterface()
        self.tx_pid_toggle         = Signal(2)

        self.handshakes_in         = HandshakeExchangeInterface(is_detector=True)
        self.handshakes_out        = HandshakeExchangeInterface(is_detector=False)
        self.issue_stall           = Signal()


class USBEndpointMultiplexer(Module):
    """ Multiplexes access to the resources shared between multiple endpoint interfaces.

    Interfaces are added using :attr:`add_interface`.

    Attributes
    ----------

    shared: EndpointInterface
        The post-multiplexer endpoint interface.
    """

    def __init__(self):

        #
        # I/O port
        #
        self.shared = EndpointInterface()

        #
        # Internals
        #
        self._interfaces = []


    def add_interface(self, interface: EndpointInterface):
        """ Adds a EndpointInterface to the multiplexer.

        Arbitration is not performed; it's expected only one endpoint will be
        driving the transmit lines at a time.
        """
        self._interfaces.append(interface)


    def _multiplex_signals(self, *, when, multiplex, sub_bus=None):
        """ Helper that creates a simple priority-encoder multiplexer.

        Parmeters
        ---------
        when: str
            The name of the interface signal that indicates that the `multiplex` signals should be
            selected for output. If this signals should be multiplexed, it should be included in `multiplex`.
        multiplex: iterable(str)
            The names of the interface signals to be multiplexed.
        """

        def get_signal(interface, name):
            """ Fetches an interface signal by name / sub_bus. """

            if sub_bus:
                bus = getattr(interface, sub_bus)
                return getattr(bus, name)
            else:
                return  getattr(interface, name)


        # Build a list of (condition, statements) tuples for Case statement
        cases = {}
        for i, interface in enumerate(self._interfaces):
            statements = []
            for signal_name in multiplex:
                # Get the actual signals for our input and output...
                driving_signal = get_signal(interface,   signal_name)
                target_signal  = get_signal(self.shared, signal_name)

                # ... and connect them.
                statements.append(target_signal.eq(driving_signal))
            cases[i] = statements

        # Create a one-hot encoder to select which interface is active
        from migen.genlib.coding import PriorityEncoder
        self.submodules._mux_enc = _mux_enc = PriorityEncoder(len(self._interfaces))
        for i, interface in enumerate(self._interfaces):
            condition = get_signal(interface, when)
            self.comb += _mux_enc.i[i].eq(condition)

        # Use the encoded value to select the interface
        self.comb += Case(_mux_enc.o, cases)


    def or_join_interface_signals(self, signal_for_interface):
        """ Joins together a set of signals on each interface by OR'ing the signals together. """

        # Find the value of all of our pre-mux signals OR'd together...
        all_signals = (signal_for_interface(i) for i in self._interfaces)
        or_value = functools.reduce(operator.__or__, all_signals, 0)

        # ... and tie it to our post-mux signal.
        self.comb += signal_for_interface(self.shared).eq(or_value)


    def do_finalize(self):
        shared = self.shared

        #
        # Pass through signals being routed -to- our pre-mux interfaces.
        #
        for interface in self._interfaces:
            self.comb += [

                # CRC and timer shared signals interface.
                interface.data_crc.crc.eq(shared.data_crc.crc),
                interface.timer.tx_allowed.eq(shared.timer.tx_allowed),
                interface.timer.tx_timeout.eq(shared.timer.tx_timeout),
                interface.timer.rx_timeout.eq(shared.timer.rx_timeout),

                # Detectors.
                interface.handshakes_in.ack.eq(shared.handshakes_in.ack),
                interface.handshakes_in.nak.eq(shared.handshakes_in.nak),
                interface.handshakes_in.stall.eq(shared.handshakes_in.stall),
                interface.handshakes_in.nyet.eq(shared.handshakes_in.nyet),

                interface.tokenizer.pid.eq(shared.tokenizer.pid),
                interface.tokenizer.address.eq(shared.tokenizer.address),
                interface.tokenizer.endpoint.eq(shared.tokenizer.endpoint),
                interface.tokenizer.new_token.eq(shared.tokenizer.new_token),
                interface.tokenizer.ready_for_response.eq(shared.tokenizer.ready_for_response),
                interface.tokenizer.frame.eq(shared.tokenizer.frame),
                interface.tokenizer.new_frame.eq(shared.tokenizer.new_frame),
                interface.tokenizer.is_in.eq(shared.tokenizer.is_in),
                interface.tokenizer.is_out.eq(shared.tokenizer.is_out),
                interface.tokenizer.is_setup.eq(shared.tokenizer.is_setup),
                interface.tokenizer.is_ping.eq(shared.tokenizer.is_ping),

                interface.clear_endpoint_halt_in.enable.eq(shared.clear_endpoint_halt_out.enable),
                interface.clear_endpoint_halt_in.direction.eq(shared.clear_endpoint_halt_out.direction),
                interface.clear_endpoint_halt_in.number.eq(shared.clear_endpoint_halt_out.number),

                # Rx interface.
                interface.rx.valid.eq(shared.rx.valid),
                interface.rx.next.eq(shared.rx.next),
                interface.rx.payload.eq(shared.rx.payload),
                interface.rx_complete.eq(shared.rx_complete),
                interface.rx_ready_for_response.eq(shared.rx_ready_for_response),
                interface.rx_invalid.eq(shared.rx_invalid),
                interface.rx_pid_toggle.eq(shared.rx_pid_toggle),

                # State signals.
                interface.speed.eq(shared.speed),
                interface.active_config.eq(shared.active_config),
                interface.active_address.eq(shared.active_address)
            ]

        #
        # Multiplex the signals being routed -from- our pre-mux interface.
        #
        self._multiplex_signals(
            when='address_changed',
            multiplex=['address_changed', 'new_address']
        )
        self._multiplex_signals(
            when='config_changed',
            multiplex=['config_changed', 'new_config']
        )

        # Connect up our transmit interface.
        self.submodules.tx_mux = tx_mux = OneHotMultiplexer(
            interface_type=USBInStreamInterface,
            mux_signals=('payload',),
            or_signals=('valid', 'first', 'last'),
            pass_signals=('ready',)
        )
        tx_mux.add_interfaces(i.tx for i in self._interfaces)
        self.comb += [
            self.shared.tx.payload.eq(tx_mux.output.payload),
            self.shared.tx.valid.eq(tx_mux.output.valid),
            self.shared.tx.first.eq(tx_mux.output.first),
            self.shared.tx.last.eq(tx_mux.output.last),
            tx_mux.output.ready.eq(self.shared.tx.ready),
        ]

        # OR together all of our handshake-generation requests...
        self.or_join_interface_signals(lambda interface : interface.handshakes_out.ack)
        self.or_join_interface_signals(lambda interface : interface.handshakes_out.nak)
        self.or_join_interface_signals(lambda interface : interface.handshakes_out.stall)

        # ... our CRC start signals...
        self.or_join_interface_signals(lambda interface : interface.data_crc.start)

        # ... and our timer start signals.
        self.or_join_interface_signals(lambda interface : interface.timer.start)

        self.or_join_interface_signals(lambda interface : interface.clear_endpoint_halt_out.enable)
        self.or_join_interface_signals(lambda interface : interface.clear_endpoint_halt_out.direction)
        self.or_join_interface_signals(lambda interface : interface.clear_endpoint_halt_out.number)

        # Finally, connect up our transmit PID select.
        conditional = If

        # We'll connect our PID toggle to whichever interface has a valid transmission going.
        past_valid  = Signal(len(self._interfaces))
        self.sync += past_valid.eq(Cat(interface.tx.valid for interface in self._interfaces))
        for i, interface in enumerate(self._interfaces):
            self.comb += If(interface.tx.valid | past_valid[i],
                shared.tx_pid_toggle.eq(interface.tx_pid_toggle)
            )
