#
# This file is part of LUNA (ported to migen).
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Endpoint interfaces for providing status updates to the host.

These are mainly meant for use with interrupt endpoints; and allow a host to e.g.
repeatedly poll a device for status.
"""

from migen import *
from migen.genlib.cdc import MultiReg

from ..endpoint import EndpointInterface


class USBSignalInEndpoint(Module):
    """ Endpoint that transmits the value of a signal to a host whenever polled.

    This is intended to be usable to implement a simple interrupt endpoint that polls for a status signal.

    Attributes
    ----------
    signal: Signal(<variable width>), input
        The signal to be relayed to the host. This signal's current value will be relayed each time the
        host polls our endpoint.
    interface: EndpointInterface
        Communications link to our USB device.

    status_read_complete: Signal(), output
        Strobe that pulses high for a single `usb`-domain cycle each time a status read is complete.

    Parameters
    ----------
    width: int
        The width of the signal we'll relay up to the host, in bits.
    endpoint_number: int
        The endpoint number (not address) this endpoint should respond to.
    endianness: str, "big" or "little", optional
        The endianness with which to send the data. Defaults to little endian.
    signal_domain: str, optional
        The name of the domain :attr:``signal`` is clocked from. If this value is anything other than
        "usb", the signal will automatically be synchronized to the USB clock domain.
    """

    def __init__(self, *, width, endpoint_number, endianness="little", signal_domain="usb"):
        self._width = width
        self._endpoint_number = endpoint_number
        self._signal_domain = signal_domain
        self._endianness = endianness

        if self._endianness not in ("big", "little"):
            raise ValueError(f"Endianness must be 'big' or 'little', not {endianness}.")

        #
        # I/O port
        #
        self.signal = Signal(self._width)
        self.interface = EndpointInterface()

        self.status_read_complete = Signal()

        #
        # Internal logic
        #

        # Shortcuts.
        tx = self.interface.tx
        tokenizer = self.interface.tokenizer

        # Grab a copy of the relevant signal that's in our USB domain; synchronizing if we need to.
        if self._signal_domain == "usb":
            target_signal = self.signal
        else:
            target_signal = Signal(self._width)
            # Use MultiReg for clock domain crossing in migen
            self.submodules += MultiReg(self.signal, target_signal, odomain="usb")

        # Store a latched version of our signal, captured before we start a transmission.
        latched_signal = Signal(self._width)

        # Grab a byte-indexable reference into our signal.
        bytes_in_signal = (self._width + 7) // 8

        # Store how many bytes we've transmitted.
        bytes_transmitted = Signal(max=bytes_in_signal + 1)

        #
        # Data transmission logic.
        #

        # If this signal is big endian, send them in reading order; otherwise, index our multiplexer in reverse.
        # Note that our signal is captured little endian by default. If we want big endian, we'll flip it.
        if self._endianness == "little":
            index_to_transmit = bytes_transmitted
        else:
            index_to_transmit = bytes_in_signal - bytes_transmitted - 1

        # Extract the current byte to transmit
        current_byte = Signal(8)
        
        # Create a combinatorial multiplexer for the byte selection
        cases = {}
        for n in range(bytes_in_signal):
            byte_start = n * 8
            byte_end = min((n + 1) * 8, self._width)
            if byte_end > byte_start:
                cases[n] = current_byte.eq(latched_signal[byte_start:byte_end])
        
        self.comb += [
            Case(index_to_transmit, cases),
            tx.payload.eq(current_byte),
        ]

        #
        # Core control FSM.
        #

        endpoint_number_matches = (tokenizer.endpoint == self._endpoint_number)
        targeting_endpoint = endpoint_number_matches & tokenizer.is_in
        packet_requested = targeting_endpoint & tokenizer.ready_for_response

        is_last_byte = Signal()
        self.comb += is_last_byte.eq(bytes_transmitted + 1 == bytes_in_signal)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")

        # IDLE -- we've not yet gotten a token requesting data. Wait for one.
        fsm.act("IDLE",
            If(packet_requested,
                NextValue(bytes_transmitted, 0),
                NextValue(latched_signal, target_signal),
                NextState("TRANSMIT_RESPONSE"),
            ),
        )

        # TRANSMIT_RESPONSE -- we're now ready to send our latched response to the host.
        fsm.act("TRANSMIT_RESPONSE",
            tx.valid.eq(1),
            tx.first.eq(bytes_transmitted == 0),
            tx.last.eq(is_last_byte),
            If(tx.ready,
                NextValue(bytes_transmitted, bytes_transmitted + 1),
                If(is_last_byte,
                    NextState("WAIT_FOR_ACK"),
                ),
            ),
        )

        # WAIT_FOR_ACK -- we've now transmitted our full packet; we need to wait for the host to ACK it
        fsm.act("WAIT_FOR_ACK",
            If(self.interface.handshakes_in.ack,
                self.status_read_complete.eq(1),
                NextValue(self.interface.tx_pid_toggle[0], ~self.interface.tx_pid_toggle[0]),
                NextState("IDLE"),
            ),
            If(self.interface.tokenizer.new_token,
                NextState("RETRANSMIT"),
            ),
        )

        # RETRANSMIT -- the host failed to ACK the data we've most recently sent.
        # Wait here for the host to request the data again.
        fsm.act("RETRANSMIT",
            If(packet_requested,
                NextValue(bytes_transmitted, 0),
                NextState("TRANSMIT_RESPONSE"),
            ),
        )
