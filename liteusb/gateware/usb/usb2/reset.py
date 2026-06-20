#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Gateware that handles USB bus resets & speed detection. """

from migen import *
from migen.genlib.fsm import FSM, NextState, NextValue

from .                     import USBSpeed
from ...interface.utmi     import UTMITransmitInterface, UTMIOperatingMode, UTMITerminationSelect


def _generate_wide_incrementer(m, platform, adder_input):
    """ Attempts to create an optimal wide-incrementer for counters.

    Yosys on certain platforms (ice40 UltraPlus) doesn't currently use hardware resources
    effectively for wide adders. We'll manually instantiate the relevant resources
    to get rid of an 18-bit carry chain; avoiding a long critical path.

    Parameters:
        platform    -- The platform we're working with.
        adder_input -- The input to our incrementer.
    """

    # If this isn't an iCE40 UltraPlus, let Yosys do its thing.
    if (not platform) or not hasattr(platform, 'device') or not platform.device.startswith('iCE40UP'):
        return adder_input + 1

    # Otherwise, we'll create a DSP adder itself.
    output = Signal.like(adder_input)
    m.specials += Instance('SB_MAC16',

        # Hook up our inputs and outputs.
        # A = upper bits of input; B = lower bits of input
        i_A      = adder_input[16:],
        i_B      = adder_input[0:16],
        o_O      = output,

        p_TOPADDSUB_UPPERINPUT =0b1,  # Use as a normal adder
        p_TOPADDSUB_CARRYSELECT=0b11, # Connect our top and bottom adders together.
        p_BOTADDSUB_UPPERINPUT =0b1,  # Use as a normal adder.
        p_BOTADDSUB_CARRYSELECT=0b01  # Always increment.
    )

    return output



class USBResetSequencer(Module):
    """ Gateware that detects reset signaling on the USB bus.

    Attributes
    ----------
    low_speed_only: Signal(), input
        If set, the device will be forced to operate as a low-speed device.
    prevent_high_speed: Signal(), input
        If set, the device will be prohibited from entering high-speed states; and will thus
        act like it's a full speed device (low_speed_only = 0).
    bus_busy: Signal(), input
        Hold-off signal that indicates that driving the bus should be delayed.
    vbus_connected: Signal(), input
        Indicates that the device is connected to VBUS. When this is de-asserted, the device will
        be held in perpetual bus reset, and reset handshaking will be disabled.
    line_state: Signal(2), input
        The UTMI linestate signals; used to read the current state of the USB D+ and D- lines.
    disconnect: Signal(), input
        If set, the device will be switched into non-driving operating mode to force a host disconnect.

    bus_reset: Signal(), output
        Strobe; pulses high for one cycle when a bus reset is detected. This signal indicates that the
         device should return to unaddressed, unconfigured, and should not longer be in High Speed mode.
    suspended: Signal(), output
        Held high while the USB device should be in suspend. This technically indicates that the device should
        drop down to consuming suspend current (<= 2.5mA), but very few devices are compliant with this requirement.
        Either way, a polite device might reduce its power consumption while in suspend.

    current_speed: Signal(2), output
        A USBSpeed value that indicates the current operating speed. Used both to drive our device's
        knowledge of operating speed and to drive our PHY's speed selection.
    operating_mode: Signal(2), output
        The current UTMI operating mode. Used to select whether we're driving the USB bus directly;
        or whether we're letting the PHY handle NRZI/bit-stuffing.
    termination_select: Signal(), output, default=1
        Determines the bus termination mode. In LS/FS, this determines the presence of our presence-detect
        pull-up. In HS mode, this determines whether the USB high-speed termination is present (0), or
        whether we're in chirp mode (1).

    tx: UTMITransmitInterface, output stream
                     -- Our UTMI transmit interface; used to drive chirp signaling onto the bus.
    """

    # Constants for our line states at various speeds.
    _LINE_STATE_SE0       = 0b00
    _LINE_STATE_SQUELCH   = 0b00
    _LINE_STATE_FS_HS_K   = 0b10
    _LINE_STATE_FS_HS_J   = 0b01
    _LINE_STATE_LS_K      = 0b01
    _LINE_STATE_LS_J      = 0b10

    # Reset time constants.
    # Eventually, if we support clocks other than 60MHz (48 MHz)?
    # We should provide the ability to scale these.
    _CYCLES_500_NANOSECONDS    = 30
    _CYCLES_1_MICROSECOND      = _CYCLES_500_NANOSECONDS  * 2
    _CYCLES_2P5_MICROSECONDS   = _CYCLES_500_NANOSECONDS  * 5
    _CYCLES_5_MICROSECONDS     = _CYCLES_1_MICROSECOND    * 5
    _CYCLES_200_MICROSECONDS   = _CYCLES_1_MICROSECOND    * 200
    _CYCLES_1_MILLISECONDS     = _CYCLES_1_MICROSECOND    * 1000
    _CYCLES_2_MILLISECONDS     = _CYCLES_1_MILLISECONDS   * 2
    _CYCLES_2P5_MILLISECONDS   = _CYCLES_2P5_MICROSECONDS * 1000
    _CYCLES_3_MILLISECONDS     = _CYCLES_1_MILLISECONDS   * 3


    def __init__(self):

        #
        # I/O port
        #
        self.low_speed_only     = Signal()
        self.full_speed_only    = Signal()

        self.bus_busy           = Signal()
        self.vbus_connected     = Signal()
        self.line_state         = Signal(2)

        self.disconnect         = Signal()

        self.bus_reset          = Signal()
        self.suspended          = Signal()

        self.current_speed      = Signal(2, reset=USBSpeed.FULL)
        self.operating_mode     = Signal(2, reset=UTMIOperatingMode.NORMAL)
        self.termination_select = Signal(1, reset=1)

        self.tx                 = UTMITransmitInterface()


    def do_finalize(self, platform=None):
        # Handle platform-specific vbus override
        if platform and hasattr(platform, 'ignore_phy_vbus') and platform.ignore_phy_vbus:
            self.vbus_connected = Signal(reset=1)

        # Event timer: keeps track of the timing of each of the individual event phases.
        timer = Signal(max=self._CYCLES_3_MILLISECONDS + 1)

        # Line state timer: keeps track of how long we've seen a line-state of interest;
        # other than a reset SE0. Used to track chirp and idle times.
        line_state_time = Signal(max=self._CYCLES_3_MILLISECONDS + 1)

        # Valid pairs: keeps track of how make Chirp K / Chirp J sequences we've
        # seen, thus far.
        valid_pairs = Signal(max=4)

        # Tracks whether we were at high speed when we entered a suspend state.
        was_hs_pre_suspend = Signal()

        # By default, always count forward in time.
        # We'll reset the timer below when appropriate.
        self.sync.usb += [
            timer.eq(_generate_wide_incrementer(self, platform, timer)),
            line_state_time.eq(_generate_wide_incrementer(self, platform, line_state_time))
        ]

        # Signal that indicates when the bus is idle.
        # Our bus's IDLE condition depends on our active speed.
        bus_idle = Signal()

        # High speed busses present SE0 (which we see as SQUELCH'd) when idle [USB2.0: 7.1.1.3].
        self.comb += [
            If(self.current_speed == USBSpeed.HIGH,
                bus_idle.eq(self.line_state == self._LINE_STATE_SQUELCH)
            ).Elif(self.current_speed == USBSpeed.FULL,
                bus_idle.eq(self.line_state == self._LINE_STATE_FS_HS_J)
            ).Else(
                bus_idle.eq(self.line_state == self._LINE_STATE_LS_J)
            )
        ]


        #
        # Core reset sequences.
        #
        fsm = FSM(reset_state="INITIALIZE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules.fsm = fsm

        # INITIALIZE -- we're immediately post-reset; we'll perform some minor setup
        fsm.act("INITIALIZE",
            # If we're working in low-speed mode, configure the PHY accordingly.
            If(self.low_speed_only,
                NextValue(self.current_speed, USBSpeed.LOW)
            ),
            NextValue(timer, 0),
            NextValue(line_state_time, 0),
            NextState("LS_FS_NON_RESET")
        )

        # LS_FS_NON_RESET -- we're currently operating at LS/FS and waiting for a reset;
        # the device could be active or inactive, but we haven't yet seen a reset condition.
        fsm.act("LS_FS_NON_RESET",
            # If we're seeing a state other than SE0 (D+ / D- at zero), this isn't yet a
            # potential reset. Keep our timer at zero.
            If(self.line_state != self._LINE_STATE_SE0,
                NextValue(timer, 0),
                # Enter forced disconnect when self.disconnect is high.
                If(self.disconnect,
                    NextState("DISCONNECT")
                )
            ),
            # If VBUS isn't connected, don't go through the whole reset process;
            # but also consider ourselves permanently in reset. This ensures we
            # don't progress through the reset FSM; but also ensures the device
            # state starts fresh with each plug.
            If(~self.vbus_connected,
                NextValue(timer, 0),
                self.bus_reset.eq(1)
            ),
            # If we see an SE0 for >2.5uS; < 3ms, this a bus reset.
            # We'll trigger a reset after 5uS; providing a little bit of timing flexibility.
            # [USB2.0: 7.1.7.5; ULPI 3.8.5.1].
            If(timer == self._CYCLES_5_MICROSECONDS,
                self.bus_reset.eq(1),
                # If we're okay to run in high speed, we'll try to perform a high-speed detect.
                If(~self.low_speed_only & ~self.full_speed_only,
                    NextState("START_HS_DETECTION")
                )
            ),
            # If we're seeing a state other than IDLE, clear our suspend timer.
            If(~bus_idle,
                NextValue(line_state_time, 0)
            ),
            # If we see 3ms of consecutive line idle, we're being put into USB suspend.
            # We'll enter our suspended state, directly. [USB2.0: 7.1.7.6]
            If(line_state_time == self._CYCLES_3_MILLISECONDS,
                NextValue(was_hs_pre_suspend, 0),
                NextState("SUSPENDED")
            )
        )


        # HS_NON_RESET -- we're currently operating at high speed and waiting for a reset or
        # suspend; the device could be active or inactive.
        fsm.act("HS_NON_RESET",
            # If we're seeing a state other than SE0 (D+ / D- at zero), this isn't yet a
            # potential reset. Keep our timer at zero.
            If(self.line_state != self._LINE_STATE_SE0,
                NextValue(timer, 0),
                # Enter forced disconnect when self.disconnect is high.
                If(self.disconnect,
                    NextState("DISCONNECT")
                )
            ),
            # If VBUS isn't connected, our device/host relationship is effectively
            # a blank state. We'll want to present our detection pull-up to the host,
            # so we'll drop out of high speed.
            If(~self.vbus_connected,
                self.bus_reset.eq(1),
                NextState("IS_LOW_OR_FULL_SPEED")
            ),
            # High speed signaling presents IDLE and RESET the same way: with the host
            # driving SE0; and us seeing SQUELCH. [USB2.0: 7.1.1.3; USB2.0: 7.1.7.6].
            # Either way, our next step is the same: we'll drop down to full-speed. [USB2.0: 7.1.7.6]
            # Afterwards, we'll take steps to differentiate a reset from a suspend.
            If(timer == self._CYCLES_3_MILLISECONDS,
                NextValue(timer, 0),
                NextValue(self.current_speed, USBSpeed.FULL),
                NextValue(self.operating_mode, UTMIOperatingMode.NORMAL),
                NextValue(self.termination_select, UTMITerminationSelect.LS_FS_NORMAL),
                NextState("DETECT_HS_SUSPEND")
            ),
            # If we see full-speed-only or low-speed-only being driven, switch
            # back to our LS/FS mode.
            If(self.full_speed_only | self.low_speed_only,
                NextState("IS_LOW_OR_FULL_SPEED")
            )
        )


        # START_HS_DETECTION -- entry state for high-speed detection
        fsm.act("START_HS_DETECTION",
            NextValue(timer, 0),
            # Switch into High-speed chirp mode. Note that we'll need to leave our
            # terminations set to '1' until we're sure this is a high-speed host;
            # or the host will see our pull-up removal as a disconnect.
            NextValue(self.current_speed, USBSpeed.HIGH),
            NextValue(self.operating_mode, UTMIOperatingMode.CHIRP),
            NextValue(self.termination_select, UTMITerminationSelect.HS_CHIRP),
            NextState("PREPARE_FOR_CHIRP_0")
        )


        # PREPARE_FOR_CHIRP_0 / PREPARE_FOR_CHIRP_1-- wait states; in which we give the PHY
        # time to the mode we'll need to drive our high-speed chirp.
        fsm.act("PREPARE_FOR_CHIRP_0",
            If(~self.bus_busy,
                NextState("PREPARE_FOR_CHIRP_1")
            )
        )

        fsm.act("PREPARE_FOR_CHIRP_1",
            If(~self.bus_busy,
                NextState("DEVICE_CHIRP")
            )
        )


        # DEVICE_CHIRP -- the device produces a 'chirp' K, which advertises to the host that
        # we're high speed capable. We'll provide that chirp K for around ~2ms. [USB2.0: 7.1.7.5]
        fsm.act("DEVICE_CHIRP",
            # Transmit a constant stream of 0's, which in this mode is a Chirp K.
            # Note that we don't need to check 'ready', as we care about the length
            # of time, rather than the number of bits.
            self.tx.valid.eq(1),
            self.tx.data.eq(0),
            # Once 2ms have passed, we can stop our chirp, and begin waiting for the
            # hosts's response. We'll wait for Ready to be asserted to do so, to ensure
            # we don't change our values in the middle of a bit.
            If(timer == self._CYCLES_2_MILLISECONDS,
                NextValue(timer, 0),
                NextValue(valid_pairs, 0),
                NextState("AWAIT_HOST_K")
            )
        )


        # AWAIT_HOST_K -- we've now completed the device chirp; and are waiting to see if the host
        # will respond with an alternating sequence of K's and J's.
        fsm.act("AWAIT_HOST_K",
            # If we don't see our response within 2.5ms, this isn't a compliant HS host. [USB2.0: 7.1.7.5].
            # This is thus a full-speed host, and we'll act as a full-speed device.
            If(timer == self._CYCLES_2P5_MILLISECONDS,
                NextState("IS_LOW_OR_FULL_SPEED")
            ),
            # Once we've seen our K, we're good to start observing J/K toggles.
            If(self.line_state == self._LINE_STATE_FS_HS_K,
                NextState("IN_HOST_K"),
                NextValue(line_state_time, 0)
            )
        )


        # IN_HOST_K: we're seeing a host Chirp K as part of our handshake; we'll
        # time it and see how long it lasts
        fsm.act("IN_HOST_K",
            # If we've exceeded our minimum chirp time, consider this a valid pattern
            # bit, # and advance in the pattern.
            If(line_state_time == self._CYCLES_2P5_MICROSECONDS,
                NextState("AWAIT_HOST_J")
            ),
            # If our input has become something other than a K, then
            # we haven't finished our sequence. We'll go back to expecting a K.
            If(self.line_state != self._LINE_STATE_FS_HS_K,
                NextState("AWAIT_HOST_K")
            ),
            # Time out if we exceed our maximum allowed duration.
            If(timer == self._CYCLES_2P5_MILLISECONDS,
                NextState("IS_LOW_OR_FULL_SPEED")
            )
        )


        # AWAIT_HOST_J -- we're waiting for the next Chirp J in the host chirp sequence
        fsm.act("AWAIT_HOST_J",
            # If we've exceeded our maximum wait, this isn't a high speed host.
            If(timer == self._CYCLES_2P5_MILLISECONDS,
                NextState("IS_LOW_OR_FULL_SPEED")
            ),
            # Once we've seen our J, start timing its duration.
            If(self.line_state == self._LINE_STATE_FS_HS_J,
                NextState("IN_HOST_J"),
                NextValue(line_state_time, 0)
            )
        )


        # IN_HOST_J: we're seeing a host Chirp K as part of our handshake; we'll
        # time it and see how long it lasts
        fsm.act("IN_HOST_J",
            # If we've exceeded our minimum chirp time, consider this a valid pattern
            # bit, and advance in the pattern.
            If(line_state_time == self._CYCLES_2P5_MICROSECONDS,
                # If this would complete our third pair, this completes a handshake,
                # and we've identified a high speed host!
                If(valid_pairs == 2,
                    NextState("IS_HIGH_SPEED")
                # Otherwise, count the pair as valid, and wait for the next K.
                ).Else(
                    NextValue(valid_pairs, valid_pairs + 1),
                    NextState("AWAIT_HOST_K")
                )
            ),
            # If our input has become something other than a K, then
            # we haven't finished our sequence. We'll go back to expecting a K.
            If(self.line_state != self._LINE_STATE_FS_HS_J,
                NextState("AWAIT_HOST_J")
            ),
            # Time out if we exceed our maximum allowed duration.
            If(timer == self._CYCLES_2P5_MILLISECONDS,
                NextState("IS_LOW_OR_FULL_SPEED")
            )
        )


        # IS_HIGH_SPEED -- we've completed a high speed handshake, and are ready to
        # switch to high speed signaling
        fsm.act("IS_HIGH_SPEED",
            # Switch to high speed.
            NextValue(timer, 0),
            NextValue(line_state_time, 0),
            NextValue(self.current_speed, USBSpeed.HIGH),
            NextValue(self.operating_mode, UTMIOperatingMode.NORMAL),
            NextValue(self.termination_select, UTMITerminationSelect.HS_NORMAL),
            NextState("HS_NON_RESET")
        )


        # IS_LOW_OR_FULL_SPEED -- we've decided the device is low/full speed (typically
        # because it didn't) complete our high-speed handshake; set it up accordingly.
        fsm.act("IS_LOW_OR_FULL_SPEED",
            NextValue(self.operating_mode, UTMIOperatingMode.NORMAL),
            NextValue(self.termination_select, UTMITerminationSelect.LS_FS_NORMAL),
            # If we're operating in low-speed only, drop down to low speed.
            If(self.low_speed_only,
                NextValue(self.current_speed, USBSpeed.LOW)
            # Otherwise, drop down to full speed.
            ).Else(
                NextValue(self.current_speed, USBSpeed.FULL)
            ),
            # Once we know that our reset is complete, move back to our normal, non-reset state.
            If(self.line_state != self._LINE_STATE_SE0,
                NextValue(timer, 0),
                NextValue(line_state_time, 0),
                NextState("LS_FS_NON_RESET")
            )
        )


        # DETECT_HS_SUSPEND -- we were operating at high speed, and just detected an event
        # which is either a reset or a suspend event; we'll now detect which.
        fsm.act("DETECT_HS_SUSPEND",
            # We've just switch from HS signaling to FS signaling.
            # We'll wait a little while for the bus to settle, and then
            # check to see if it's settled to FS idle; or if we still see SE0.
            If(timer == self._CYCLES_200_MICROSECONDS,
                NextValue(timer, 0),
                # If we've resume IDLE, this is suspend. Move to HS suspend.
                If(self.line_state == self._LINE_STATE_FS_HS_J,
                    NextValue(was_hs_pre_suspend, 1),
                    NextState("SUSPENDED")
                # Otherwise, this is a reset (or, if K/SE1, we're very confused, and
                # should re-initialize anyway). Move to the HS reset detect sequence.
                ).Else(
                    self.bus_reset.eq(1),
                    NextState("START_HS_DETECTION")
                )
            )
        )


        # SUSPEND -- our device has entered USB suspend; we'll now wait for either a
        # resume or a reset
        is_ls_k = Signal()
        is_fs_k = Signal()
        self.comb += [
            is_ls_k.eq(self.low_speed_only & (self.line_state == self._LINE_STATE_LS_K)),
            is_fs_k.eq(~self.low_speed_only & (self.line_state == self._LINE_STATE_FS_HS_K))
        ]

        fsm.act("SUSPENDED",
            self.suspended.eq(1),
            # If we see a K state, then we're being resumed.
            If(is_ls_k | is_fs_k,
                NextValue(timer, 0),
                # If we were in high-speed pre-suspend, then resume being in HS.
                If(was_hs_pre_suspend,
                    NextState("IS_HIGH_SPEED")
                # Otherwise, just resume.
                ).Else(
                    NextValue(timer, 0),
                    NextValue(line_state_time, 0),
                    NextState("LS_FS_NON_RESET")
                )
            ),
            # If this isn't an SE0, we're not receiving a reset request.
            # Keep our reset counter at zero.
            If(self.line_state != self._LINE_STATE_SE0,
                NextValue(timer, 0)
            ),
            # If we see an SE0 for > 2.5uS, this is a reset request. [USB 2.0: 7.1.7.5]
            # We'll handle it directly from suspend.
            If(timer == self._CYCLES_2P5_MICROSECONDS,
                self.bus_reset.eq(1),
                NextValue(timer, 0),
                # If we're limited to LS or FS, move to the appropriate state.
                If(self.low_speed_only | self.full_speed_only,
                    NextValue(timer, 0),
                    NextValue(line_state_time, 0),
                    NextState("LS_FS_NON_RESET")
                # Otherwise, this could be a high-speed device; enter its reset.
                ).Else(
                    NextState("START_HS_DETECTION")
                )
            )
        )


        # DISCONNECT -- our device has entered a forced USB disconnect; hold the device in
        # NON_DRIVING operating mode for Tddis=0.25us and wait for self.disconnect to go low.
        tddis = Signal()

        fsm.act("DISCONNECT",
            NextValue(self.operating_mode, UTMIOperatingMode.NON_DRIVING),
            # A disconnect condition is indicated if the host or hub is not driving the data lines and an
            # SE0 persists on a downstream facing port for more than Tddis.
            # [USB2.0: 7.1.7.3].
            If(timer == self._CYCLES_2P5_MICROSECONDS,
                NextValue(tddis, 1)
            ),
            # Exit DISCONNECT once the Tddis timer has expired and self.disconnect is low.
            If((~self.disconnect) & tddis,
                NextValue(tddis, 0),
                NextValue(self.current_speed, USBSpeed.FULL),
                NextValue(self.operating_mode, UTMIOperatingMode.NORMAL),
                NextValue(self.termination_select, 1),
                NextState("INITIALIZE")
            )
        )
