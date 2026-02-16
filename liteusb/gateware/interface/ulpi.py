# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" ULPI interfacing hardware. Ported from Amaranth to Migen. """

from migen import *
from migen.genlib.fsm import FSM, NextState
from migen.genlib.record import Record


class ULPIInterface(Record):
    """ Record that represents a standard ULPI interface. """

    def __init__(self):
        super().__init__([
            ("data", [
                ("i", 8),
                ("o", 8),
                ("oe", 1),
            ]),
            ("clk", 1),
            ("nxt", 1),
            ("stp", 1),
            ("dir", [
                ("i", 1),
            ]),
            ("rst", 1),
        ])


class ULPIRegisterWindow(Module):
    """ Gateware interface that handles ULPI register reads and writes.

    I/O ports:

        # ULPI signals:
        I: ulpi_data_in[8]   -- input value of the ULPI data lines
        O: ulpi_data_out[8]  -- output value of the ULPI data lines
        O: ulpi_out_en       -- true iff we're trying to drive the ULPI data lines

        # Controller signals:
        O: busy              -- indicates when the register window is busy processing a transaction
        I: address[6]        -- the address of the register to work with
        O: done              -- strobe that indicates when a register request is complete

        I: read_request      -- strobe that requests a register read
        O: read_data[8]      -- data read from the relevant register read

        I: write_request     -- strobe that indicates a register write
        I: write_data[8]     -- data to be written during a register write

    """

    COMMAND_REG_WRITE = 0b10000000
    COMMAND_REG_READ  = 0b11000000

    def __init__(self):

        #
        # I/O port.
        #

        self.ulpi_data_in  = Signal(8)
        self.ulpi_data_out = Signal(8)
        self.ulpi_out_req  = Signal()
        self.ulpi_dir      = Signal()
        self.ulpi_next     = Signal()
        self.ulpi_stop     = Signal()

        self.busy          = Signal()
        self.address       = Signal(6)
        self.done          = Signal()

        self.read_request  = Signal()
        self.read_data     = Signal(8)

        self.write_request = Signal()
        self.write_data    = Signal(8)

        #
        # Internal signals
        #
        self.current_address = Signal(6)
        self.current_write   = Signal(8)

    def do_finalize(self):
        # Keep our control signals low unless explicitly asserted.
        self.sync.usb += [
            self.ulpi_out_req.eq(0),
            self.ulpi_stop   .eq(0),
            self.done        .eq(0)
        ]

        fsm = FSM(reset_state="IDLE")
        self.submodules.fsm = fsm

        # We're busy whenever we're not IDLE; indicate so.
        self.comb += self.busy.eq(~fsm.ongoing("IDLE"))

        # IDLE: wait for a request to be made
        fsm.act("IDLE",
            # Apply a NOP whenever we're idle.
            self.ulpi_data_out.eq(0),

            # Constantly latch in our arguments while IDLE.
            self.current_address.eq(self.address),
            self.current_write.eq(self.write_data),

            If(self.read_request,
                NextState("START_READ")
            ).Elif(self.write_request,
                NextState("START_WRITE")
            )
        )

        #
        # Read handling.
        #

        # START_READ: wait for the bus to be idle, so we can transmit.
        fsm.act("START_READ",
            If(~self.ulpi_dir,
                self.ulpi_data_out.eq(self.COMMAND_REG_READ | self.address),
                self.ulpi_out_req.eq(1),
                NextState("SEND_READ_ADDRESS")
            )
        )

        # SEND_READ_ADDRESS: Request sending the read address
        fsm.act("SEND_READ_ADDRESS",
            self.ulpi_out_req.eq(1),

            If(self.ulpi_dir,
                self.ulpi_out_req.eq(0),
                NextState("START_READ")
            ).Elif(self.ulpi_next,
                self.ulpi_out_req.eq(0),
                self.ulpi_data_out.eq(0),
                NextState("READ_TURNAROUND")
            )
        )

        # READ_TURNAROUND: wait for the PHY to take control of the ULPI bus.
        fsm.act("READ_TURNAROUND",
            NextState("READ_COMPLETE")
        )

        # READ_COMPLETE: the ULPI read exchange is complete
        fsm.act("READ_COMPLETE",
            self.read_data.eq(self.ulpi_data_in),
            self.done.eq(1),
            NextState("IDLE")
        )

        #
        # Write handling.
        #

        # START_WRITE: wait for the bus to be idle
        fsm.act("START_WRITE",
            If(~self.ulpi_dir,
                self.ulpi_data_out.eq(self.COMMAND_REG_WRITE | self.address),
                self.ulpi_out_req.eq(1),
                NextState("SEND_WRITE_ADDRESS")
            )
        )

        # SEND_WRITE_ADDRESS: Continue sending the write address
        fsm.act("SEND_WRITE_ADDRESS",
            self.ulpi_out_req.eq(1),

            If(self.ulpi_dir,
                self.ulpi_out_req.eq(0),
                NextState("START_WRITE")
            ).Elif(self.ulpi_next,
                self.ulpi_data_out.eq(self.write_data),
                NextState("HOLD_WRITE")
            )
        )

        # Hold the write data on the bus
        fsm.act("HOLD_WRITE",
            self.ulpi_out_req.eq(1),

            If(self.ulpi_dir,
                self.ulpi_out_req.eq(0),
                NextState("START_WRITE")
            ).Elif(self.ulpi_next,
                self.ulpi_data_out.eq(0),
                self.ulpi_stop.eq(1),
                NextState("STOPPING")
            )
        )

        fsm.act("STOPPING",
            self.ulpi_stop.eq(0),

            If(self.ulpi_dir,
                self.ulpi_out_req.eq(0),
                NextState("START_WRITE")
            ).Else(
                self.ulpi_out_req.eq(0),
                self.done.eq(1),
                NextState("IDLE")
            )
        )


class ULPIRxEventDecoder(Module):
    """ Simple piece of gateware that tracks receive events.

    I/O port:

        I: ulpi_data_in[8] -- The current input state of the ULPI data lines.
        I: ulpi_dir        -- The ULPI bus-direction signal.
        I: ulpi_nxt        -- The ULPI 'next' throttle signal.
        I: register_operation_in_progress
            Signal that should be true iff we're performing a register operation.

        O: last_rx_command -- The full byte value of the last RxCmd.

        O: line_state[2]   -- The states of the two USB lines.
        O: rx_active       -- True when a packet receipt is active.
        O: rx_error        -- True when a packet receive error has occurred.
        O: host_disconnect -- True if the host has just disconnected.
        O: id_digital      -- Digital value of the ID pin.
        O: vbus_valid      -- True iff a valid VBUS voltage is present
        O: session_end     -- True iff a session has just ended.

        # Strobes indicating signal changes.
        O: rx_start        -- True iff an RxEvent has changed the value of RxActive from 0 -> 1.
        O: rx_stop         -- True iff an RxEvent has changed the value of RxActive from 1 -> 0.
    """

    def __init__(self, *, ulpi_bus):

        #
        # I/O port.
        #
        self.ulpi = ulpi_bus
        self.register_operation_in_progress = Signal()

        # Optional: signal that allows access to the last RxCmd byte.
        self.last_rx_command = Signal(8)

        self.line_state      = Signal(2)
        self.rx_active       = Signal()
        self.rx_error        = Signal()
        self.host_disconnect = Signal()
        self.id_digital      = Signal()
        self.vbus_valid      = Signal()
        self.session_valid   = Signal()
        self.session_end     = Signal()

        # RxActive strobes.
        self.rx_start        = Signal()
        self.rx_stop         = Signal()

    def do_finalize(self):
        # An RxCmd is present when three conditions are met:
        # - We're not actively undergoing a register read.
        # - Direction has been high for more than one cycle.
        # - NXT is low.

        # Create a delayed version of DIR
        direction_delayed = Signal()
        self.sync.usb += direction_delayed.eq(self.ulpi.dir.i)

        receiving = Signal()
        self.comb += receiving.eq(direction_delayed & self.ulpi.dir.i)

        # Default our strobes to 0
        self.sync.usb += [
            self.rx_start.eq(0),
            self.rx_stop.eq(0)
        ]

        # Sample the DATA lines whenever these conditions are met
        rx_active_bit = self.ulpi.data.i[4]
        self.sync.usb += [
            If(receiving & ~self.ulpi.nxt & ~self.register_operation_in_progress,
                self.last_rx_command.eq(self.ulpi.data.i),

                If(~self.rx_active & rx_active_bit,
                    self.rx_start.eq(1)
                ),
                If(self.rx_active & ~rx_active_bit,
                    self.rx_stop.eq(1)
                )
            )
        ]

        # Break the most recent RxCmd into its UTMI-equivalent signals.
        # From table 3.8.1.2 in the ULPI spec; rev 1.1/Oct-20-2004.
        self.comb += [
            self.line_state.eq(self.last_rx_command[0:2]),
            self.vbus_valid.eq(self.last_rx_command[2:4] == 0b11),
            self.session_valid.eq(self.last_rx_command[2:4] == 0b10),
            self.session_end.eq(self.last_rx_command[2:4] == 0b00),
            self.rx_active.eq(self.last_rx_command[4]),
            self.rx_error.eq(self.last_rx_command[4:6] == 0b11),
            self.host_disconnect.eq(self.last_rx_command[4:6] == 0b10),
            self.id_digital.eq(self.last_rx_command[6]),
        ]


class ULPIControlTranslator(Module):
    """ Gateware that translates ULPI control signals to their UTMI equivalents.

    I/O port:
        I: bus_idle       -- Indicates that the ULPI bus is idle, and thus capable of
                             performing register writes.

        I: xcvr_select[2] -- selects the operating speed of the transciever;
                             00 = HS, 01 = FS, 10 = LS, 11 = LS on FS bus
        I: term_select    -- enables termination for the given operating mode; see spec
        I: op_mode        -- selects the operating mode of the transciever;
                             00 = normal, 01 = non-driving, 10 = disable bit-stuff/NRZI
        I: suspend        -- places the transceiver into suspend mode; active high

        I: id_pullup      -- when set, places a 100kR pull-up on the ID pin
        I: dp_pulldown    -- when set, enables a 15kR pull-down on D+; intended for host mode
        I: dm_pulldown    -- when set, enables a 15kR pull-down on D+; intended for host mode

        I: chrg_vbus      -- when set, connects a resistor from VBUS to GND to discharge VBUS
        I: dischrg_vbus   -- when set, connects a resistor from VBUS to 3V3 to charge VBUS above SessValid

        O: busy           -- true iff the control translator is actively performing an operation
    """

    def __init__(self, *, register_window, own_register_window=False):
        """
        Parameters:
            register_window     -- The ULPI register window to work with.
            own_register_window -- True iff we're the owner of this register window.
                                   Typically, we'll use the register window for a broader controller;
                                   but this can be set to True to indicate that we need to consider this
                                   register window our own, and thus a submodule.
        """

        self.register_window     = register_window
        self.own_register_window = own_register_window

        #
        # I/O port
        #
        self.bus_idle      = Signal()
        self.xcvr_select   = Signal(2, reset=0b01)
        self.term_select   = Signal()
        self.op_mode       = Signal(2)
        self.suspend       = Signal()

        self.id_pullup     = Signal()
        self.dp_pulldown   = Signal(reset=1)
        self.dm_pulldown   = Signal(reset=1)

        self.chrg_vbus     = Signal()
        self.dischrg_vbus  = Signal()

        self.busy          = Signal()

        # Extra/non-UTMI properties.
        self.use_external_vbus_indicator = Signal(reset=1)

        #
        # Internal variables.
        #
        self._register_signals = {}

    def add_composite_register(self, address, value, *, reset_value=0):
        """ Adds a ULPI register that's composed of multiple control signals.

        Params:
            address      -- The register number in the ULPI register space.
            value       -- An 8-bit signal composing the bits that should be placed in
                           the given register.

            reset_value -- If provided, the given value will be assumed as the reset value
                        -- of the given register; allowing us to avoid an initial write.
        """

        current_register_value = Signal(8, reset=reset_value, name=f"current_register_value_{address:02x}")

        # Create internal signals that request register updates.
        write_requested = Signal(name=f"write_requested_{address:02x}")
        write_value     = Signal(8, name=f"write_value_{address:02x}")
        write_done      = Signal(name=f"write_done_{address:02x}")

        self._register_signals[address] = {
            'write_requested': write_requested,
            'write_value':     write_value,
            'write_done':      write_done
        }

        # If we've just finished a write, update our current register value.
        self.sync.usb += If(write_done,
            current_register_value.eq(write_value)
        )

        # If we have a mismatch between the requested and actual register value,
        # request a write of the new value.
        self.comb += write_requested.eq(current_register_value != value)
        self.sync.usb += If(current_register_value != value,
            write_value.eq(value)
        )

    def populate_ulpi_registers(self):
        """ Creates translator objects that map our control signals to ULPI registers. """

        # Function control.
        function_control = Cat(self.xcvr_select, self.term_select, self.op_mode, Constant(0, 1), ~self.suspend, Constant(0, 1))
        self.add_composite_register(0x04, function_control, reset_value=0b01000001)

        # OTG control.
        otg_control = Cat(
            self.id_pullup, self.dp_pulldown, self.dm_pulldown, self.dischrg_vbus,
            self.chrg_vbus, Constant(0, 1), Constant(0, 1), self.use_external_vbus_indicator
        )
        self.add_composite_register(0x0A, otg_control, reset_value=0b00000110)

    def do_finalize(self):
        if self.own_register_window:
            self.submodules.reg_window = self.register_window

        # Add the registers that represent each of our signals.
        self.populate_ulpi_registers()

        # Build the register handling logic
        # For simplicity in migen, we handle one register at a time
        has_any_request = Signal()
        
        requests = []
        for address, signals in self._register_signals.items():
            requests.append(signals['write_requested'])
        
        if requests:
            self.comb += has_any_request.eq(Cat(*requests).bool())

        # Handle each register
        for address, signals in self._register_signals.items():
            request_write = signals['write_requested'] & ~self.register_window.done & self.bus_idle

            self.comb += [
                If(signals['write_requested'],
                    signals['write_done'].eq(self.register_window.done),
                    self.register_window.address.eq(address),
                    self.register_window.write_data.eq(signals['write_value']),
                    self.register_window.write_request.eq(request_write),
                )
            ]
            
            self.sync.usb += If(signals['write_requested'],
                self.busy.eq(request_write | self.register_window.busy)
            )

        # If no register accesses are active
        self.comb += If(~has_any_request,
            self.register_window.write_request.eq(0)
        )
        self.sync.usb += If(~has_any_request,
            self.busy.eq(self.register_window.busy)
        )

        # Ensure our register window is never performing a read.
        self.comb += self.register_window.read_request.eq(0)


class ULPITransmitTranslator(Module):
    """ Accepts UTMI transmit signals, and converts them into ULPI equivalents.

    I/O port:
        I: tx_data[8]      -- The data to be transmitted.
        I: tx_valid        -- Driven high to indicate we're trying to transmit.
        O: tx_ready        -- Driven high when a given byte will be accepted on tx_data on the next clock edge.

        I: op_mode[2]      -- The UTMI operating mode. Used to determine when NOPID commands should be issued;
                              and when to force transmit errors.

        I: bus_idle        -- Should be asserted when the transmitter is able to control the bus.

        O: ulpi_data_out   -- The data to be driven onto the ULPI transmit lines.
        O: ulpi_out_req    -- Asserted when we're trying to drive the ULPI data lines.
        I: ulpi_nxt        -- The NXT signal for the relevant ULPI bus.
        O: ulpi_stp        -- The STP signal for the relevant ULPI bus.

        O: busy            -- True iff this module is using the bus.
    """


    # Prefix for ULPI transmit commands.
    TRANSMIT_COMMAND = 0b01000000

    # UTMI operating mode for "bit stuffing disabled".
    OP_MODE_NO_BIT_STUFFING = 0b10


    def __init__(self):

        #
        # I/O port.
        #

        self.tx_data         = Signal(8)
        self.tx_valid        = Signal()
        self.tx_ready        = Signal()

        self.op_mode         = Signal(2)
        self.bus_idle        = Signal()

        self.ulpi_out_req    = Signal()
        self.ulpi_data_out   = Signal.like(self.tx_data)
        self.ulpi_nxt        = Signal()
        self.ulpi_stp        = Signal()

        self.busy            = Signal()

    def do_finalize(self):
        bit_stuffing_disabled = (self.op_mode == self.OP_MODE_NO_BIT_STUFFING)

        fsm = FSM(reset_state="IDLE")
        self.submodules.fsm = fsm

        # Mark ourselves as busy whenever we're not in idle.
        self.comb += self.busy.eq(~fsm.ongoing("IDLE"))

        # IDLE: our transmitter is ready
        fsm.act("IDLE",
            self.ulpi_stp.eq(0),

            If(self.tx_valid & self.bus_idle,
                If(bit_stuffing_disabled,
                    self.ulpi_out_req.eq(1),
                    self.ulpi_data_out.eq(self.TRANSMIT_COMMAND),
                    self.tx_ready.eq(0),

                    If(self.ulpi_nxt,
                        NextState("TRANSMIT")
                    )
                ).Else(
                    self.ulpi_out_req.eq(1),
                    self.ulpi_data_out.eq(self.TRANSMIT_COMMAND | self.tx_data[0:4]),
                    self.tx_ready.eq(self.ulpi_nxt),

                    If(self.ulpi_nxt,
                        NextState("TRANSMIT")
                    )
                )
            )
        )

        # TRANSMIT: we're in the body of a transmit
        fsm.act("TRANSMIT",
            self.ulpi_data_out.eq(self.tx_data),
            self.tx_ready.eq(self.ulpi_nxt),
            self.ulpi_stp.eq(0),

            If(~self.tx_valid,
                self.ulpi_out_req.eq(0),
                self.ulpi_stp.eq(1),

                If(bit_stuffing_disabled,
                    self.ulpi_data_out.eq(0xFF)
                ).Else(
                    self.ulpi_data_out.eq(0)
                ),
                
                NextState("IDLE")
            )
        )


class UTMITranslator(Module):
    """ Gateware that translates a ULPI interface into a simpler UTMI one.

    I/O port:

        O: busy          -- signal that's true iff the ULPI interface is being used
                            for a register or transmit command

        # See the UTMI specification for most signals.

        # Data signals:
        I: tx_data[8]  -- data to be transmitted; valid when tx_valid is asserted
        I: tx_valid    -- set to true when data is to be transmitted; indicates the data_in
                          byte is valid; de-asserting this line terminates the transmission
        O: tx_ready    -- indicates the the PHY is ready to accept a new byte of data, and that the
                          transmitter should move on to the next byte after the given cycle

        O: rx_data[8]  -- data received from the PHY; valid when rx_valid is asserted
        O: rx_valid    -- indicates that the data present on rx_data is new and valid data;
                          goes high for a single ULPI clock cycle to indicate new data is ready

        O: rx_active   -- indicates that the PHY is actively receiving data from the host; data is
                          slewed on rx_data by rx_valid
        O: rx_error    -- indicates that an error has occurred in the current transmission

        # Extra signals:
        O: rx_complete -- strobe that goes high for one cycle when a packet rx is complete

        # Signals for diagnostic use:
        O: last_rxcmd    -- The byte content of the last RxCmd.

        I: address       -- The ULPI register address to work with.
        O: read_data[8]  -- The contents of the most recently read ULPI command.
        I: write_data[8] -- The data to be written on the next write request.
        I: manual_read   -- Strobe that triggers a diagnostic read.
        I: manual_write  -- Strobe that triggers a diagnostic write.

    """

    _CYCLES_1_MILLISECONDS = 60000

    # UTMI status signals translated from the ULPI bus.
    RXEVENT_STATUS_SIGNALS = [
        ('line_state', 2), ('vbus_valid', 1), ('session_valid', 1), ('session_end', 1),
        ('rx_error',   1), ('host_disconnect', 1), ('id_digital', 1)
    ]

    # Control signals that we control through our control translator.
    CONTROL_SIGNALS = [
        ('xcvr_select',  2), ('term_select', 1), ('op_mode',     2), ('suspend',   1),
        ('id_pullup',    1), ('dm_pulldown', 1), ('dp_pulldown', 1), ('chrg_vbus', 1),
        ('dischrg_vbus', 1), ('use_external_vbus_indicator', 1)
    ]


    def __dir__(self):
        """ Extend our properties list of contain all of the above fields, for proper autocomplete. """

        properties = list(super().__dir__())

        properties.extend(name for name, _ in self.RXEVENT_STATUS_SIGNALS)
        properties.extend(name for name, _ in self.CONTROL_SIGNALS)

        return properties


    def __init__(self, *, ulpi, use_platform_registers=True, handle_clocking=True):
        """ Params:

            ulpi                   -- The ULPI bus to communicate with.
            use_platform_registers -- If True (or not provided), any extra registers writes provided in
                                      the platform definition will be applied automatically.
            handle_clocking        -- True iff we should attempt to automatically handle ULPI clocking. If
                                      the `clk` ULPI signal is an input, it will be used to provide the 'usb'
                                      domain clock. If the ULPI signal is an output, it will driven with our
                                      'usb' domain clock. If False, it will be the user's responsibility to
                                      handle clocking.

            Note that it's recommended that multi-PHY systems either use a single clock for all PHYs
            (assuming the PHYs support clock input), or that individual clock domains be created for each
            PHY using a DomainRenamer.
        """

        self.use_platform_registers = use_platform_registers
        self.handle_clocking        = handle_clocking

        #
        # I/O port
        #
        self.ulpi            = ulpi
        self.busy            = Signal()

        # Data signals.
        self.rx_data         = Signal(8)
        self.rx_valid        = Signal()

        self.tx_data         = Signal(8)
        self.tx_valid        = Signal()
        self.tx_ready        = Signal()

        # Status signals.
        self.rx_active       = Signal()

        # RxEvent-based flags.
        for signal_name, size in self.RXEVENT_STATUS_SIGNALS:
            self.__dict__[signal_name] = Signal(size, name=signal_name)

        # Control signals.
        for signal_name, size in self.CONTROL_SIGNALS:
            self.__dict__[signal_name] = Signal(size, name=signal_name)

        # Diagnostic I/O.
        self.last_rx_command = Signal(8)

        #
        # Internal
        #

        #  Create a list of extra registers to be set.
        self._extra_registers = {}

    def add_extra_register(self, write_address, write_value, *, default_value=None):
        """ Adds logic to configure an extra ULPI register. Useful for configuring vendor registers.

        Params:
            write_address -- The write address of the target ULPI register.
            write_value   -- The value to be written. If a Signal is provided; the given register will be
                             set post-reset, if necessary; and then dynamically updated each time the signal changes.
                             If an integer constant is provided, this value will be written once upon startup.
            default_value -- The default value the register is expected to have post-reset; used to determine
                             if the value needs to be updated post-reset. If a Signal is provided for write_value,
                             this must be provided; if an integer is provided for write_value, this is optional.
        """

        # Ensure we have a default_value if we have a Signal(); as this will determine
        # whether we need to update the register post-reset.
        if (default_value is None) and isinstance(write_value, Signal):
            raise ValueError("if write_value is a signal, default_value must be provided")

        # Otherwise, we'll pick a value that ensures the write always occurs.
        elif default_value is None:
            default_value = 0xff ^ write_value

        self._extra_registers[write_address] = {'value': write_value, 'default': default_value}

    def do_finalize(self):
        # Create the component parts of our ULPI interfacing hardware.
        register_window     = ULPIRegisterWindow()
        control_translator  = ULPIControlTranslator(register_window=register_window)
        rxevent_decoder     = ULPIRxEventDecoder(ulpi_bus=self.ulpi)
        transmit_translator = ULPITransmitTranslator()
        
        self.submodules.register_window     = register_window
        self.submodules.control_translator  = control_translator
        self.submodules.rxevent_decoder     = rxevent_decoder
        self.submodules.transmit_translator = transmit_translator

        # Use standard usb domain
        raw_clock_domain = 'usb'

        # Add any extra registers provided by the user to our control translator.
        for address, values in self._extra_registers.items():
            control_translator.add_composite_register(address, values['value'], reset_value=values['default'])

        # Keep track of when any of our components are busy
        any_busy = \
            register_window.busy     | \
            transmit_translator.busy | \
            control_translator.busy  | \
            self.ulpi.dir.i

        # If we're handling ULPI clocking, do so.
        if self.handle_clocking:
            if hasattr(self.ulpi.clk, 'oe'):
                raise TypeError("ULPI records with bidirectional clock lines require manual handling.")
            elif hasattr(self.ulpi.clk, 'o'):
                self.comb += self.ulpi.clk.o.eq(ClockSignal(raw_clock_domain))
            elif hasattr(self.ulpi.clk, 'i'):
                # Note: Clock signal assignment in migen is different
                pass
            else:
                raise TypeError(f"ULPI `clk` was an unexpected type {type(self.ulpi.clk)}." \
                    " You may need to handle clocking manually.")

        # Hook up our reset signal iff our ULPI bus has one.
        phy_ready = Signal()
        if hasattr(self.ulpi, 'rst'):
            self.comb += self.ulpi.rst.o.eq(ResetSignal(raw_clock_domain))

            # After reset, DIR may not be driven high immediately.
            # Before using the bus, wait for the minimum Tstart time.
            startup_counter = Signal(max=self._CYCLES_1_MILLISECONDS + 1)
            self.sync.usb += [
                startup_counter.eq(startup_counter + 1),
                If(startup_counter == self._CYCLES_1_MILLISECONDS,
                    phy_ready.eq(1)
                )
            ]
        else:
            self.sync.usb += phy_ready.eq(1)

        # Connect our ULPI control signals to each of our subcomponents.
        self.comb += [
            # Drive the bus whenever the target PHY isn't.
            self.ulpi.data.oe.eq(~self.ulpi.dir.i),

            # Generate our busy signal.
            self.busy.eq(any_busy),

            # Connect our data inputs to the event decoder.
            rxevent_decoder.register_operation_in_progress.eq(register_window.busy),
            self.last_rx_command.eq(rxevent_decoder.last_rx_command),

            # Connect our inputs to our transmit translator.
            transmit_translator.ulpi_nxt.eq(self.ulpi.nxt),
            transmit_translator.op_mode.eq(self.op_mode),
            transmit_translator.bus_idle.eq(~control_translator.busy & ~self.ulpi.dir.i & phy_ready),
            transmit_translator.tx_data.eq(self.tx_data),
            transmit_translator.tx_valid.eq(self.tx_valid),
            self.tx_ready.eq(transmit_translator.tx_ready),

            # Connect our inputs to our control translator / register window.
            control_translator.bus_idle.eq(~transmit_translator.busy & phy_ready),
            register_window.ulpi_data_in.eq(self.ulpi.data.i),
            register_window.ulpi_dir.eq(self.ulpi.dir.i),
            register_window.ulpi_next.eq(self.ulpi.nxt),
        ]

        # Control our the source of our ULPI data output.
        self.comb += [
            If(transmit_translator.ulpi_out_req,
                self.ulpi.data.o.eq(transmit_translator.ulpi_data_out),
                self.ulpi.stp.eq(transmit_translator.ulpi_stp)
            ).Else(
                self.ulpi.data.o.eq(register_window.ulpi_data_out),
                self.ulpi.stp.eq(register_window.ulpi_stop)
            )
        ]

        # Connect our RxEvent status signals from our RxEvent decoder.
        for signal_name, _ in self.RXEVENT_STATUS_SIGNALS:
            signal = getattr(rxevent_decoder, signal_name)
            self.comb += self.__dict__[signal_name].eq(signal)

        # Connect our control signals through the control translator.
        for signal_name, _ in self.CONTROL_SIGNALS:
            signal = getattr(control_translator, signal_name)
            self.comb += signal.eq(self.__dict__[signal_name])

        # RxActive handler
        past_dir        = Signal.like(self.ulpi.dir.i)
        dir_rising_edge = ~past_dir & self.ulpi.dir.i
        dir_based_start = dir_rising_edge & self.ulpi.nxt

        self.sync.usb += [
            past_dir.eq(self.ulpi.dir.i),
            If(~self.ulpi.dir.i | rxevent_decoder.rx_stop,
                self.rx_active.eq(0)
            ).Elif(dir_based_start | rxevent_decoder.rx_start,
                self.rx_active.eq(1)
            )
        ]

        # Data-out and RxValid
        self.sync.usb += [
            self.rx_data.eq(self.ulpi.data.i),
            self.rx_valid.eq(self.ulpi.nxt & self.rx_active)
        ]
