#
# This file is part of LITEUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
"""
Contains the organizing hardware used to add USB Device functionality
to your own designs; including the core :class:`USBDevice` class.
"""

import logging

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.fsm import FSM, NextState, NextValue

from usb_protocol.emitters import DeviceDescriptorCollection

from ..interface.ulpi import UTMITranslator
from ..interface.utmi import UTMIInterfaceMultiplexer
from ..interface.gateware_phy import GatewarePHY

from .usb2.packet import USBTokenDetector, USBHandshakeGenerator, USBDataPacketCRC
from .usb2.packet import USBInterpacketTimer, USBDataPacketGenerator, USBHandshakeDetector
from .usb2.packet import USBDataPacketReceiver
from .usb2.reset import USBResetSequencer

from .usb2.endpoint import USBEndpointMultiplexer
from .usb2.control import USBControlEndpoint


class USBDevice(Module):
    """ Core gateware common to all LITEUSB USB2 devices.

    The ``USBDevice`` module contains the low-level communications hardware necessary to implement a USB device;
    including hardware for maintaining device state, detecting events, reading data from the host, and generating
    responses.

    This class can be instantiated directly, and used to build a USB device,
    or can be subclassed to create custom device types.

    To configure a ``USBDevice`` from a CPU or other wishbone master, see :class:`USBDeviceController`;
    which can be easily attached using its `attach` method.


    Parameters
    ----------

    bus: [UTMI interface, ULPI Interface]
        The UTMI or ULPI PHY connection to be used for communications.

    handle_clocking: bool, Optional
        True iff we should attempt to connect up the `usb` clock domain to the PHY
        automatically based on the clk signals's I/O direction. This option may not work
        for non-simple connections; in which case you will need to connect the clock signal
        yourself.


    Attributes
    ----------

    connect: Signal(), input
        Held high to keep the current USB device connected; or held low to disconnect.
    low_speed_only: Signal(), input
        If high, the device will operate at low speed.
    full_speed_only: Signal(), input
        If high, the device will be prohibited from operating at high speed.

    frame_number: Signal(11), output
        The current USB frame number.
    microframe_number: Signal(3), output
        The current USB microframe number. Always 0 on non-HS connections.
    sof_detected: Signal(), output
        Pulses for one cycle each time a SOF is detected; and thus our frame number has changed.
    new_frame: Signal(), output
        Strobe that indicates a new frame (not microframe) is detected.

    reset_detected: Signal(), output
        Asserted when the USB device receives a bus reset.

    # State signals.
    suspended: Signal(), output
        High when the device is in USB suspend. This can be (and by the spec must be) used to trigger
        the device to enter lower-power states.

    tx_activity_led: Signal(), output
        Signal that can be used to drive an activity LED for TX.
    rx_activity_led: Signal(), output
        Signal that can be used to drive an activity LED for RX.

    """

    def __init__(self, bus, handle_clocking=True, register_tx_outputs=False):
        """
        Parameters:
            register_tx_outputs -- If True and a ULPI bus is used, the ULPI
                TX data/stp outputs are registered before the pins (timing
                closure at 60MHz ULPI, at the cost of one cycle of latency).
        """
        self.handle_clocking = handle_clocking
        self.bus = bus

        # If this looks more like a ULPI bus than a UTMI bus, translate it.
        if hasattr(bus, 'dir'):
            self.utmi = UTMITranslator(ulpi=bus, handle_clocking=handle_clocking,
                register_outputs=register_tx_outputs)
            self.bus_busy = self.utmi.busy
            self.translator = self.utmi
            self.always_fs = False
            self.data_clock = 60e6

        # If this looks more like raw I/O connections than a UTMI bus, create a pure-gatware
        # PHY to drive the raw I/O signals.
        elif hasattr(bus, 'd_n'):
            self.utmi = GatewarePHY(io=bus)
            self.bus_busy = Signal(reset=0)
            self.translator = self.utmi
            self.always_fs = True
            self.data_clock = 12e6

        # Otherwise, use it directly.
        # Note that since a true UTMI interface has separate Tx/Rx/control
        # interfaces, we don't need to care about bus 'busyness'; so we'll
        # set it to a const zero.
        else:
            self.utmi = bus
            self.bus_busy = Signal(reset=0)
            self.translator = None
            self.always_fs = True
            self.data_clock = 12e6

        #
        # I/O port
        #
        self.connect = Signal()
        self.low_speed_only = Signal()
        self.full_speed_only = Signal()

        self.frame_number = Signal(11)
        self.microframe_number = Signal(3)
        self.sof_detected = Signal()
        self.new_frame = Signal()
        self.reset_detected = Signal()

        self.speed = Signal(2)
        self.suspended = Signal()
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()

        #
        # Internals.
        #
        self._endpoints = []

        # Try to retrieve the bus name, needed for USB device hooks from platform
        self._bus_name = None
        try:
            if hasattr(bus, 'name'):
                self._bus_name = bus.name
            elif hasattr(bus, 'clk'):
                # PureInterface does not have a name attribute, but we can use the
                # first item of a path tuple
                self._bus_name = bus.clk.path[0]
        except (AttributeError, TypeError):
            pass

    def add_endpoint(self, endpoint):
        """ Adds an endpoint interface to the device.

        Parameters
        ----------
        endpoint: Elaborateable
            The endpoint interface to be added. Can be any piece of gateware with a
            :class:`EndpointInterface` attribute called ``interface``.
        """
        self._endpoints.append(endpoint)

    def add_control_endpoint(self):
        """ Adds a basic control endpoint to the device.

        Does not add any request handlers. If you want standard request handlers;
        :attr:`add_standard_control_endpoint` automatically adds standard request handlers.

        Returns
        -------
        Returns the endpoint object for the control endpoint.
        """
        control_endpoint = USBControlEndpoint(utmi=self.utmi)
        self.add_endpoint(control_endpoint)

        return control_endpoint

    def add_standard_control_endpoint(self, descriptors, **kwargs):
        """ Adds a control endpoint with standard request handlers to the device.

        Parameters will be passed on to StandardRequestHandler.

        Return value
        ------------
        The endpoint object created.
        """

        # Create our endpoint, and add standard descriptors to it.
        control_endpoint = USBControlEndpoint(utmi=self.utmi)
        control_endpoint.add_standard_request_handlers(descriptors, **kwargs)
        self.add_endpoint(control_endpoint)

        return control_endpoint

    def do_finalize(self):
        # If we have a bus translator, include it in our submodules.
        if self.translator:
            self.submodules.translator = self.translator

        #
        # Internal device state.
        #

        # Stores the device's current address. Used to identify which packets are for us.
        address = Signal(7, reset=0)

        # Stores the device's current configuration. Defaults to unconfigured.
        configuration = Signal(8, reset=0)

        #
        # Internal interconnections.
        #

        # Create our reset sequencer, which will be in charge of detecting USB port resets,
        # detecting high-speed hosts, and communicating that we are a high speed device.
        self.submodules.reset_sequencer = reset_sequencer = USBResetSequencer()

        self.comb += [
            reset_sequencer.bus_busy.eq(self.bus_busy),

            reset_sequencer.vbus_connected.eq(~self.utmi.session_end),
            reset_sequencer.line_state.eq(self.utmi.line_state),

            reset_sequencer.disconnect.eq(~self.connect),
        ]

        # Create our internal packet components:
        # - A token detector, which will identify and parse the tokens that start transactions.
        # - A data transmitter, which will transmit provided data streams.
        # - A data receiver, which will receive data from UTMI and convert it into streams.
        # - A handshake generator, which will assist in generating response packets.
        # - A handshake detector, which detects handshakes generated by the host.
        # - A data CRC16 handler, which will compute data packet CRCs.
        # - An interpacket delay timer, which will enforce interpacket delays.
        self.submodules.token_detector = token_detector = \
            USBTokenDetector(utmi=self.utmi, domain_clock=self.data_clock, fs_only=self.always_fs)
        self.submodules.transmitter = transmitter = USBDataPacketGenerator()
        self.submodules.receiver = receiver = USBDataPacketReceiver(utmi=self.utmi)
        self.submodules.handshake_generator = handshake_generator = USBHandshakeGenerator()
        self.submodules.handshake_detector = handshake_detector = USBHandshakeDetector(utmi=self.utmi)
        self.submodules.data_crc = data_crc = USBDataPacketCRC()
        self.submodules.timer = timer = \
            USBInterpacketTimer(domain_clock=self.data_clock, fs_only=self.always_fs)

        # Connect our transmitter/receiver to our CRC generator.
        data_crc.add_interface(transmitter.crc)
        data_crc.add_interface(receiver.data_crc)

        # Connect our receiver to our timer.
        timer.add_interface(receiver.timer)

        self.comb += [
            # Ensure our token detector only responds to tokens addressed to us.
            token_detector.address.eq(address),

            # Hook up our data_crc to our receive inputs.
            data_crc.rx_data.eq(self.utmi.rx_data),
            data_crc.rx_valid.eq(self.utmi.rx_valid),

            # Connect our state signals to our subordinate components.
            token_detector.speed.eq(self.speed),
            timer.speed.eq(self.speed)
        ]

        #
        # Endpoint connections.
        #

        # Create our endpoint multiplexer...
        self.submodules.endpoint_mux = endpoint_mux = USBEndpointMultiplexer()
        endpoint_collection = endpoint_mux.shared

        # Connect our timer and CRC interfaces.
        timer.add_interface(endpoint_collection.timer)
        data_crc.add_interface(endpoint_collection.data_crc)

        self.comb += [
            # Low-level hardware interface.
            token_detector.interface.connect(endpoint_collection.tokenizer),
            handshake_detector.detected.connect(endpoint_collection.handshakes_in),

            # Device state.
            endpoint_collection.speed.eq(self.speed),
            endpoint_collection.active_config.eq(configuration),
            endpoint_collection.active_address.eq(address),

            # Receive interface.
            receiver.stream.connect(endpoint_collection.rx),
            endpoint_collection.rx_complete.eq(receiver.packet_complete),
            endpoint_collection.rx_invalid.eq(receiver.crc_mismatch),
            endpoint_collection.rx_ready_for_response.eq(receiver.ready_for_response),
            endpoint_collection.rx_pid_toggle.eq(receiver.active_pid[3]),

            # Transmit interface.
            endpoint_collection.tx.attach(transmitter.stream),
            handshake_generator.issue_ack.eq(endpoint_collection.handshakes_out.ack),
            handshake_generator.issue_nak.eq(endpoint_collection.handshakes_out.nak),
            handshake_generator.issue_stall.eq(endpoint_collection.handshakes_out.stall),
            transmitter.data_pid.eq(endpoint_collection.tx_pid_toggle),
        ]

        # If an endpoint wants to update our address or configuration, accept the update.
        self.sync.usb += [
            If(endpoint_collection.address_changed,
                address.eq(endpoint_collection.new_address)
            ),
            If(endpoint_collection.config_changed,
                configuration.eq(endpoint_collection.new_config)
            )
        ]

        # Finally, add each of our endpoints to this module and our multiplexer.
        for endpoint in self._endpoints:

            # Create a display name for the endpoint...
            name = endpoint.__class__.__name__
            if hasattr(self.submodules, name):
                name = f"{name}_{id(endpoint)}"

            # ... and add it, both as a submodule and to our multiplexer.
            endpoint_mux.add_interface(endpoint.interface)
            setattr(self.submodules, name, endpoint)

        #
        # Transmitter multiplexing.
        #

        # Create a multiplexer that will arbitrate access to the transmit lines.
        self.submodules.tx_multiplexer = tx_multiplexer = UTMIInterfaceMultiplexer()

        # Connect each of our transmitters.
        tx_multiplexer.add_input(reset_sequencer.tx)
        tx_multiplexer.add_input(transmitter.tx)
        tx_multiplexer.add_input(handshake_generator.tx)

        self.comb += [
            # Connect our transmit multiplexer to the actual UTMI bus.
            tx_multiplexer.output.attach(self.utmi),

            # Connect up the transmit CRC interface to our UTMI bus.
            data_crc.tx_valid.eq(tx_multiplexer.output.valid & self.utmi.tx_ready),
            data_crc.tx_data.eq(tx_multiplexer.output.data),
        ]

        #
        # Device-state management.
        #

        # On a bus reset, clear our address and configuration.
        self.sync.usb += [
            If(reset_sequencer.bus_reset,
                address.eq(0),
                configuration.eq(0),
            )
        ]

        # Device operating state controls.
        self.comb += [
            # Disable our host-mode pulldowns; as we're a device.
            self.utmi.dm_pulldown.eq(0),
            self.utmi.dp_pulldown.eq(0),

            # Let our reset sequencer set our USB mode and speed.
            reset_sequencer.low_speed_only.eq(self.low_speed_only & ~self.always_fs),
            reset_sequencer.full_speed_only.eq(self.full_speed_only | self.always_fs),
            self.utmi.op_mode.eq(reset_sequencer.operating_mode),
            self.utmi.xcvr_select.eq(reset_sequencer.current_speed),
            self.utmi.term_select.eq(reset_sequencer.termination_select & self.connect),
        ]

        #
        # Frame/microframe state.
        #

        # Handle each new SOF token as we receive them.
        # new_frame is combinational so the microframe counter sees it in the same cycle.
        self.comb += self.new_frame.eq(
            token_detector.interface.new_frame &
            (token_detector.interface.frame != self.frame_number)
        )

        self.sync.usb += [
            If(token_detector.interface.new_frame,
                # Update our knowledge of the current frame number.
                self.frame_number.eq(token_detector.interface.frame),

                # If this is a new frame, our microframe count should be zero.
                If(self.new_frame,
                    self.microframe_number.eq(0)
                ).Else(
                    # Otherwise, this SOF indicates a new _microframe_ [USB 2.0: 8.4.3.1].
                    self.microframe_number.eq(self.microframe_number + 1)
                )
            )
        ]

        #
        # Device-state outputs.
        #
        self.comb += [
            self.speed.eq(reset_sequencer.current_speed),
            self.suspended.eq(reset_sequencer.suspended),

            self.sof_detected.eq(token_detector.interface.new_frame),
            self.reset_detected.eq(reset_sequencer.bus_reset),

            self.tx_activity_led.eq(tx_multiplexer.output.valid),
            self.rx_activity_led.eq(self.utmi.rx_valid)
        ]
