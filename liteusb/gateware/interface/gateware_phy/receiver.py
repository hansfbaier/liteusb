# BSD 3-Clause License
#
# Adapted from ValentyUSB.
#
# Copyright (c) 2020, Great Scott Gadgets <ktemkin@greatscottgadgets.com>
# Copyright (c) 2018, Luke Valenty
# Copyright (c) 2025, Hans Baier <foss@hans-baier.de>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

from migen import Module, Signal, Cat, ClockSignal
from migen.genlib.cdc import MultiReg
from migen.genlib.fifo import AsyncFIFO


class RxClockDataRecovery(Module):
    """RX Clock Data Recovery module.

    RxClockDataRecovery synchronizes the USB differential pair with the FPGAs
    clocks, de-glitches the differential pair, and recovers the incoming clock
    and data.

    Clock Domain
    ------------
    usb_48 : 48MHz

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    usbp_raw : Signal(1)
        Raw USB+ input from the FPGA IOs, no need to synchronize.

    usbn_raw : Signal(1)
        Raw USB- input from the FPGA IOs, no need to synchronize.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.
    The line_state_dj/dk/se0/se1 outputs are 1-hot encoded.

    line_state_valid : Signal(1)
        Asserted for one clock when the output line state is ready to be sampled.

    line_state_dj : Signal(1)
        Represents Full Speed J-state on the incoming USB data pair.
        Qualify with line_state_valid.

    line_state_dk : Signal(1)
        Represents Full Speed K-state on the incoming USB data pair.
        Qualify with line_state_valid.

    line_state_se0 : Signal(1)
        Represents SE0 on the incoming USB data pair.
        Qualify with line_state_valid.

    line_state_se1 : Signal(1)
        Represents SE1 on the incoming USB data pair.
        Qualify with line_state_valid.
    """
    def __init__(self, usbp_raw, usbn_raw):
        self._usbp = usbp_raw
        self._usbn = usbn_raw


        self.line_state_valid = Signal()
        self.line_state_dj = Signal()
        self.line_state_dk = Signal()
        self.line_state_se0 = Signal()
        self.line_state_se1 = Signal()

        # Synchronize the USB signals at our I/O boundary.
        # Despite the assumptions made in ValentyUSB, this line rate recovery FSM
        # isn't enough to properly synchronize these inputs. We'll explicitly synchronize.
        sync_dp = Signal()
        sync_dn = Signal()
        self.submodules.dp_cdc = MultiReg(self._usbp, sync_dp, odomain="usb_io")
        self.submodules.dn_cdc = MultiReg(self._usbn, sync_dn, odomain="usb_io")

        #######################################################################
        # Line State Recovery State Machine
        #
        # The receive path doesn't use a differential receiver.  Because of
        # this there is a chance that one of the differential pairs will appear
        # to have changed to the new state while the other is still in the old
        # state.  The following state machine detects transitions and waits an
        # extra sampling clock before decoding the state on the differential
        # pair.  This transition period  will only ever last for one clock as
        # long as there is no noise on the line.  If there is enough noise on
        # the line then the data may be corrupted and the packet will fail the
        # data integrity checks.
        #
        dpair =  Cat(sync_dp, sync_dn)

        # output signals for use by the clock recovery stage
        line_state_in_transition = Signal()

        # FSM state encoding
        fsm_state = Signal(3)
        STATE_DT  = 0
        STATE_DJ  = 1
        STATE_DK  = 2
        STATE_SE0 = 3
        STATE_SE1 = 4

        # Update outputs based on current state
        self.sync.usb_io += [
            self.line_state_se0.eq(fsm_state == STATE_SE0),
            self.line_state_se1.eq(fsm_state == STATE_SE1),
            self.line_state_dj.eq(fsm_state == STATE_DJ),
            self.line_state_dk.eq(fsm_state == STATE_DK),
        ]

        # FSM logic
        self.sync.usb_io += [
            If(fsm_state == STATE_DT,
                # If we are in a transition state, then we can sample the pair and
                # move to the next corresponding line state.
                Case(dpair, {
                    0b10: fsm_state.eq(STATE_DJ),
                    0b01: fsm_state.eq(STATE_DK),
                    0b00: fsm_state.eq(STATE_SE0),
                    0b11: fsm_state.eq(STATE_SE1),
                })
            ).Elif(fsm_state == STATE_DJ,
                # If we are in a valid line state and the value of the pair changes,
                # then we need to move to the transition state.
                If(dpair != 0b10,
                    fsm_state.eq(STATE_DT)
                )
            ).Elif(fsm_state == STATE_DK,
                If(dpair != 0b01,
                    fsm_state.eq(STATE_DT)
                )
            ).Elif(fsm_state == STATE_SE0,
                If(dpair != 0b00,
                    fsm_state.eq(STATE_DT)
                )
            ).Elif(fsm_state == STATE_SE1,
                If(dpair != 0b11,
                    fsm_state.eq(STATE_DT)
                )
            )
        ]

        self.comb += line_state_in_transition.eq(fsm_state == STATE_DT)


        #######################################################################
        # Clock and Data Recovery
        #
        # The DT state from the line state recovery state machine is used to align to
        # transmit clock.  The line state is sampled in the middle of the bit time.
        #
        # Example of signal relationships
        # -------------------------------
        # line_state        DT  DJ  DJ  DJ  DT  DK  DK  DK  DK  DK  DK  DT  DJ  DJ  DJ
        # line_state_valid  ________----____________----____________----________----____
        # bit_phase         0   0   1   2   3   0   1   2   3   0   1   2   0   1   2
        #

        # We 4x oversample, so make the line_state_phase have
        # 4 possible values.
        line_state_phase = Signal(2)

        self.sync.usb_io += [
            If(line_state_in_transition,
                # re-align the phase with the incoming transition
                line_state_phase.eq(0),

                # make sure we never assert valid on a transition
                self.line_state_valid.eq(0),
            ).Else(
                # keep tracking the clock by incrementing the phase
                line_state_phase.eq(line_state_phase + 1),
                self.line_state_valid.eq(line_state_phase == 1)
            )
        ]


class RxNRZIDecoder(Module):
    """RX NRZI decoder.

    In order to ensure there are enough bit transitions for a receiver to recover
    the clock usb uses NRZI encoding.  This module processes the incoming
    dj, dk, se0, and valid signals and decodes them to data values.  It
    also pipelines the se0 signal and passes it through unmodified.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.8
    https://en.wikipedia.org/wiki/Non-return-to-zero

    Clock Domain
    ------------
    usb_48 : 48MHz

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_valid : Signal(1)
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_dj : Signal(1)
        Indicates the bus is currently in a Full-Speed J-state.
        Qualified by valid.

    i_dk : Signal(1)
        Indicates the bus is currently in a Full-Speed K-state.
        Qualified by valid.

    i_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.

    o_valid : Signal(1)
        Qualifier for all of the output signals. Indicates one bit of valid
        data is present on the outputs.

    o_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    o_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_dj = Signal()
        self.i_dk = Signal()
        self.i_se0 = Signal()

        # pass all of the outputs through a pipe stage
        self.o_valid = Signal(1)
        self.o_data = Signal(1)
        self.o_se0 = Signal(1)

        last_data = Signal()
        self.sync.usb_io += [
            If(self.i_valid,
                last_data.eq(self.i_dk),
                self.o_data.eq(~(self.i_dk ^ last_data)),
                self.o_se0.eq((~self.i_dj) & (~self.i_dk)),
            ),
            self.o_valid.eq(self.i_valid)
        ]


class RxPacketDetect(Module):
    """Packet Detection

    Full Speed packets begin with the following sequence:

        KJKJKJKK

    This raw sequence corresponds to the following data:

        00000001

    The bus idle condition is signaled with the J state:

        JJJJJJJJ

    This translates to a series of '1's since there are no transitions.  Given
    this information, it is easy to detect the beginning of a packet by looking
    for 00000001.

    The end of a packet is even easier to detect.  The end of a packet is
    signaled with two SE0 and one J.  We can just look for the first SE0 to
    detect the end of the packet.

    Packet detection can occur in parallel with bitstuff removal.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.10

    Input Ports
    ------------
    i_valid : Signal(1)
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    i_se0 : Signal(1)
        Indicator for SE0 from USB bus.
        Qualified by valid.

    Output Ports
    ------------
    o_pkt_start : Signal(1)
        Asserted for one clock on the last bit of the sync.

    o_pkt_active : Signal(1)
        Asserted while in the middle of a packet.

    o_pkt_end : Signal(1)
        Asserted for one clock after the last data bit of a packet was received.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_data = Signal()
        self.i_se0 = Signal()

        self.o_pkt_start = Signal()
        self.o_pkt_active = Signal()
        self.o_pkt_end = Signal()

        # FSM state encoding - using a 3-bit signal
        fsm_state = Signal(3)
        STATE_D0 = 0
        STATE_D1 = 1
        STATE_D2 = 2
        STATE_D3 = 3
        STATE_D4 = 4
        STATE_D5 = 5
        STATE_PKT_ACTIVE = 6

        # Internal signals
        pkt_start = Signal()
        pkt_active = Signal()
        pkt_end = Signal()

        # FSM logic for states D0-D4
        self.sync.usb_io += [
            If(fsm_state == STATE_D0,
                If(self.i_valid,
                    If(self.i_data | self.i_se0,
                        # Receiving '1' or SE0 early resets the packet start counter.
                        fsm_state.eq(STATE_D0)
                    ).Else(
                        # Receiving '0' increments the packet start counter.
                        fsm_state.eq(STATE_D1)
                    )
                )
            ).Elif(fsm_state == STATE_D1,
                If(self.i_valid,
                    If(self.i_data | self.i_se0,
                        fsm_state.eq(STATE_D0)
                    ).Else(
                        fsm_state.eq(STATE_D2)
                    )
                )
            ).Elif(fsm_state == STATE_D2,
                If(self.i_valid,
                    If(self.i_data | self.i_se0,
                        fsm_state.eq(STATE_D0)
                    ).Else(
                        fsm_state.eq(STATE_D3)
                    )
                )
            ).Elif(fsm_state == STATE_D3,
                If(self.i_valid,
                    If(self.i_data | self.i_se0,
                        fsm_state.eq(STATE_D0)
                    ).Else(
                        fsm_state.eq(STATE_D4)
                    )
                )
            ).Elif(fsm_state == STATE_D4,
                If(self.i_valid,
                    If(self.i_data | self.i_se0,
                        fsm_state.eq(STATE_D0)
                    ).Else(
                        fsm_state.eq(STATE_D5)
                    )
                )
            ).Elif(fsm_state == STATE_D5,
                If(self.i_valid,
                    If(self.i_se0,
                        fsm_state.eq(STATE_D0)
                    ).Elif(self.i_data,
                        # once we get a '1', the packet is active
                        fsm_state.eq(STATE_PKT_ACTIVE)
                    )
                )
            ).Elif(fsm_state == STATE_PKT_ACTIVE,
                If(self.i_valid & self.i_se0,
                    fsm_state.eq(STATE_D0)
                )
            )
        ]

        # Combinational outputs
        self.comb += [
            pkt_active.eq(fsm_state == STATE_PKT_ACTIVE),
            pkt_start.eq((fsm_state == STATE_D5) & self.i_valid & self.i_data),
            pkt_end.eq((fsm_state == STATE_PKT_ACTIVE) & self.i_valid & self.i_se0),
        ]

        # pass all of the outputs through a pipe stage
        self.comb += [
            self.o_pkt_start.eq(pkt_start),
            self.o_pkt_active.eq(pkt_active),
            self.o_pkt_end.eq(pkt_end),
        ]



class RxBitstuffRemover(Module):
    """RX Bitstuff Removal

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.  This extra bit is required to recover the
    clock, but it should not be passed on to higher layers in the device.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

    Clock Domain
    ------------
    usb_48 : 48MHz

    Input Ports
    ------------
    i_valid : Signal(1)
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    Output Ports
    ------------
    o_data : Signal(1)
        Decoded data bit from USB bus.

    o_stall : Signal(1)
        Indicates the bit stuffer just removed an extra bit, so no data available.

    o_error : Signal(1)
        Indicates there has been a bitstuff error. A bitstuff error occurs
        when there should be a stuffed '0' after 6 consecutive 1's; but instead
        of a '0', there is an additional '1'.  This is normal during IDLE, but
        should never happen within a packet.
        Qualified by valid.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_data = Signal()

        # pass all of the outputs through a pipe stage
        self.o_data = Signal()
        self.o_error = Signal()
        self.o_stall = Signal(reset=1)

        # This state machine recognizes sequences of 6 bits and drops the 7th
        # bit.  The fsm implements a counter in a series of several states.
        # This is intentional to help absolutely minimize the levels of logic
        # used.
        drop_bit = Signal(1)

        # FSM state encoding
        fsm_state = Signal(3)
        STATE_D0 = 0
        STATE_D1 = 1
        STATE_D2 = 2
        STATE_D3 = 3
        STATE_D4 = 4
        STATE_D5 = 5
        STATE_D6 = 6

        # FSM logic for states D0-D5
        self.sync.usb_io += [
            If(fsm_state == STATE_D0,
                If(self.i_valid,
                    If(self.i_data,
                        # Receiving '1' increments the bitstuff counter.
                        fsm_state.eq(STATE_D1)
                    ).Else(
                        # Receiving '0' resets the bitstuff counter.
                        fsm_state.eq(STATE_D0)
                    )
                )
            ).Elif(fsm_state == STATE_D1,
                If(self.i_valid,
                    If(self.i_data,
                        fsm_state.eq(STATE_D2)
                    ).Else(
                        fsm_state.eq(STATE_D0)
                    )
                )
            ).Elif(fsm_state == STATE_D2,
                If(self.i_valid,
                    If(self.i_data,
                        fsm_state.eq(STATE_D3)
                    ).Else(
                        fsm_state.eq(STATE_D0)
                    )
                )
            ).Elif(fsm_state == STATE_D3,
                If(self.i_valid,
                    If(self.i_data,
                        fsm_state.eq(STATE_D4)
                    ).Else(
                        fsm_state.eq(STATE_D0)
                    )
                )
            ).Elif(fsm_state == STATE_D4,
                If(self.i_valid,
                    If(self.i_data,
                        fsm_state.eq(STATE_D5)
                    ).Else(
                        fsm_state.eq(STATE_D0)
                    )
                )
            ).Elif(fsm_state == STATE_D5,
                If(self.i_valid,
                    If(self.i_data,
                        fsm_state.eq(STATE_D6)
                    ).Else(
                        fsm_state.eq(STATE_D0)
                    )
                )
            ).Elif(fsm_state == STATE_D6,
                # Reset the bitstuff counter, drop the data.
                fsm_state.eq(STATE_D0)
            )
        ]

        self.comb += drop_bit.eq(fsm_state == STATE_D6)

        self.sync.usb_io += [
            self.o_data.eq(self.i_data),
            self.o_stall.eq(drop_bit | ~self.i_valid),
            self.o_error.eq(drop_bit & self.i_data & self.i_valid),
        ]


class RxShifter(Module):
    """RX Shifter

    A shifter is responsible for shifting in serial bits and presenting them
    as parallel data.  The shifter knows how many bits to shift and has
    controls for resetting the shifter.

    Clock Domain
    ------------
    usb    : 12MHz

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Number of bits to shift in.

    Input Ports
    -----------
    i_valid : Signal(1)
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_data : Signal(1)
        Serial input data.
        Qualified by valid.

    Output Ports
    ------------
    o_data : Signal(width)
        Shifted in data.

    o_put : Signal(1)
        Asserted for one clock once the register is full.
    """
    def __init__(self, width):
        self._width = width

        #
        # I/O port
        #
        self.reset   = Signal()

        self.i_valid = Signal()
        self.i_data  = Signal()

        self.o_data  = Signal(width)
        self.o_put   = Signal()

        # Instead of using a counter, we will use a sentinel bit in the shift
        # register to indicate when it is full.
        shift_reg = Signal(width+1, reset=0b1)

        self.comb += self.o_data.eq(shift_reg[0:width])
        self.sync.usb_io += self.o_put.eq(shift_reg[width-1] & ~shift_reg[width] & self.i_valid)

        self.sync.usb_io += [
            If(self.reset,
                shift_reg.eq(1)
            ).Elif(self.i_valid,
                If(shift_reg[width],
                    shift_reg.eq(Cat(self.i_data, 1))
                ).Else(
                    shift_reg.eq(Cat(self.i_data, shift_reg[0:width]))
                )
            )
        ]


class RxPipeline(Module):

    def __init__(self):
        self.reset = Signal()

        # 12MHz USB alignment pulse in 48MHz clock domain
        self.o_bit_strobe = Signal()

        # Reset state is J
        self.i_usbp = Signal(reset=1)
        self.i_usbn = Signal(reset=0)

        self.o_data_strobe = Signal()
        self.o_data_payload = Signal(8)

        self.o_pkt_start = Signal()
        self.o_pkt_in_progress = Signal()
        self.o_pkt_end = Signal()

        self.o_receive_error = Signal()

        #
        # Clock/Data recovery.
        #
        clock_data_recovery = RxClockDataRecovery(self.i_usbp, self.i_usbn)
        self.submodules.clock_data_recovery = clock_data_recovery
        self.comb += self.o_bit_strobe.eq(clock_data_recovery.line_state_valid)

        #
        # NRZI decoding
        #
        nrzi = RxNRZIDecoder()
        self.submodules.nrzi = nrzi
        self.comb += [
            nrzi.i_valid.eq(self.o_bit_strobe),
            nrzi.i_dj.eq(clock_data_recovery.line_state_dj),
            nrzi.i_dk.eq(clock_data_recovery.line_state_dk),
            nrzi.i_se0.eq(clock_data_recovery.line_state_se0),
        ]

        #
        # Packet boundary detection.
        #
        detect = RxPacketDetect()
        self.submodules.detect = detect
        self.comb += [
            detect.i_valid.eq(nrzi.o_valid),
            detect.i_se0.eq(nrzi.o_se0),
            detect.i_data.eq(nrzi.o_data),
        ]

        #
        # Bitstuff remover.
        #
        bitstuff = RxBitstuffRemover()
        self.submodules.bitstuff = bitstuff
        self.comb += [
            bitstuff.i_valid.eq(nrzi.o_valid),
            bitstuff.i_data.eq(nrzi.o_data),
            self.o_receive_error.eq(bitstuff.o_error)
        ]

        #
        # 1bit->8bit (1byte) gearing
        #
        shifter = RxShifter(width=8)
        self.submodules.shifter = shifter
        past_o_pkt_active    = Signal()
        self.sync.usb_io += past_o_pkt_active.eq(detect.o_pkt_active)
        self.comb += [
            shifter.reset.eq(detect.o_pkt_end),
            shifter.i_data.eq(bitstuff.o_data),
            shifter.i_valid.eq(~bitstuff.o_stall & past_o_pkt_active),
        ]

        #
        # Clock domain crossing.
        #
        flag_start  = Signal()
        flag_end    = Signal()
        flag_valid  = Signal()

        # Async FIFO for payload (8-bit width, depth 4)
        payload_fifo = AsyncFIFO(width=8, depth=4)
        self.submodules.payload_fifo = payload_fifo
        self.comb += [
            payload_fifo.din.eq(shifter.o_data[::-1]),
            payload_fifo.we.eq(shifter.o_put),
            self.o_data_payload.eq(payload_fifo.dout),
            self.o_data_strobe.eq(payload_fifo.readable),
            payload_fifo.re.eq(1)
        ]

        # Async FIFO for flags (2-bit width, depth 4)
        flags_fifo = AsyncFIFO(width=2, depth=4)
        self.submodules.flags_fifo = flags_fifo
        self.comb += [
            flags_fifo.din[1].eq(detect.o_pkt_start),
            flags_fifo.din[0].eq(detect.o_pkt_end),
            flags_fifo.we.eq(detect.o_pkt_start | detect.o_pkt_end),

            flag_start.eq(flags_fifo.dout[1]),
            flag_end.eq(flags_fifo.dout[0]),
            flag_valid.eq(flags_fifo.readable),
            flags_fifo.re.eq(1),
        ]

        # Packet flag signals (in 12MHz domain)
        self.comb += [
            self.o_pkt_start.eq(flag_start & flag_valid),
            self.o_pkt_end.eq(flag_end & flag_valid),
        ]

        self.sync.usb += [
            If(self.o_pkt_start,
                self.o_pkt_in_progress.eq(1)
            ).Elif(self.o_pkt_end,
                self.o_pkt_in_progress.eq(0)
            )
        ]
