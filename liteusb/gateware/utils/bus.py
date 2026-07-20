#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Utilities for working with busses. """

import operator
import functools

from migen import *
from migen.genlib.coding import PriorityEncoder


class OneHotMultiplexer(Module):
    """ Gateware that merges a collection of busses into a single bus.

    The busses joined must meet the following conditions:
        - The relevant type must have a signal that indicates when its data should be
          passed through to the relevant output. This is the 'valid' field.
        - Only one of the relevant busses `valid` fields should be high at a time;
          this effectively makes all of the high signals together a one-hot encoding.
          The implementation's behavior if more than one `valid` signal is undefined.

    I/O port:
        O*: output -- Our output interface; carries the signal merged from all input busses.
    """

    def __init__(self, *, interface_type, valid_field='valid', mux_signals=(), or_signals=(), pass_signals=()):
        """
        Parameters:
            interface_type  -- The type of interface we'll be multiplexing.
            valid_field     -- The name of the field that indicates the relevant object's validity.
            mux_signals     -- An iterable of {signal names to be multiplexed, or functions that
                               accept instances of the relevant interface type and return a Signal}.
                               Signals listed here are passed through iff their one-hot `valid` signal is high.
            or_signals      -- An itereable of {signals names to be multiplexed, or functions that accept
                               an instance of the relevant interface type and return a Signal}. Signals listed
                               here are OR'd together without multiplexing; it's expected that these signals will
                               only be high when their corresponding `valid` signal is high.
            pass_signals    -- A list of signals that should be passed back from the output interface to each
                               of our input interfaces.
        """

        self._valid_field  = valid_field
        self._mux_signals  = mux_signals
        self._or_signals   = or_signals
        self._pass_signals = pass_signals

        # Collection that stores each of the interfaces added to this bus.
        self._inputs = []

        #
        # I/O port
        #
        self.output = interface_type()


    def add_interface(self, input_interface):
        """ Adds an interface to the multiplexer. """
        self._inputs.append(input_interface)


    def add_interfaces(self, interfaces):
        """ Adds a collection/iterable of interfaces to the multiplexer. """
        for interface in interfaces:
            self.add_interface(interface)


    def add_input(self, input_interface):
        """ Alias for add_interface. Adds an interface to the multiplexer. """
        self.add_interface(input_interface)


    @staticmethod
    def _get_signal(interface, name_or_function):
        """ Fetches a signal from the given interface.

        Parameter:
            interface        -- The interface to fetch the relevant signal from.
            name_or_function -- The name of the signal to retrieve; or a function that
                                returns the relevant signal given the interface.
         """

        if callable(name_or_function):
            return name_or_function(interface)
        else:
            return getattr(interface, name_or_function)


    def do_finalize(self):
        #
        # Our module has three core parts:
        #   - an encoder, which converts from our one-hot signal to a mux select line
        #   - a multiplexer, which handles multiplexing e.g. payload signals
        #   - a set of OR'ing logic, which joints together our simple or'd signals

        # Create our encoder...
        self.submodules.encoder = encoder = PriorityEncoder(len(self._inputs))
        for index, interface in enumerate(self._inputs):

            # ... and tie its inputs to each of our 'valid' signals.
            valid_signal = getattr(interface, self._valid_field)
            self.comb += encoder.i[index].eq(valid_signal)


        # Drive each of our output signals from a one-hot AND-OR tree.
        # (Functionally identical to the previous PriorityEncoder+Case
        # implementation, but ~2 logic levels instead of a long linear
        # priority chain; this matters on the ULPI 60MHz comb path.)
        for identifier in self._mux_signals:
            output_signal = self._get_signal(self.output, identifier)
            terms = []
            for interface in self._inputs:
                valid_signal = getattr(interface, self._valid_field)
                input_signal = self._get_signal(interface, identifier)
                terms.append(Mux(valid_signal, input_signal, 0))
            self.comb += output_signal.eq(functools.reduce(operator.__or__, terms, 0))


        # Create the OR'ing logic for each of or or_signals.
        for identifier in self._or_signals:

            # Figure out the signals we want to work with...
            output_signal = self._get_signal(self.output, identifier)
            input_signals = (self._get_signal(i, identifier) for i in self._inputs)

            # ... and OR them together.
            or_reduced = functools.reduce(operator.__or__, input_signals, 0)
            self.comb += output_signal.eq(or_reduced)


        # Finally, pass each of our pass-back signals from the output interface
        # back to each of our input interfaces.
        for identifier in self._pass_signals:
            output_signal = self._get_signal(self.output, identifier)

            for interface in self._inputs:
                input_signal = self._get_signal(interface, identifier)
                self.comb += input_signal.eq(output_signal)
