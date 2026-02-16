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

from migen import Module, Signal, Cat
from migen.genlib.cdc import MultiReg



class TxShifter(Module):
    """Transmit Shifter

    TxShifter accepts parallel data and shifts it out serially.

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the data to be shifted.

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_data: Signal(width)
        Data to be transmitted.

    i_enable: Signal(), input
        When asserted, shifting will be allowed; otherwise, the shifter will be stalled.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_data : Signal()
        Serial data output.

    o_empty : Signal()
        Asserted the cycle before the shifter loads in more i_data.

    o_get : Signal()
        Asserted the cycle after the shifter loads in i_data.

    """
    def __init__(self, width):
        self._width = width

        #
        # I/O Port
        #
        self.i_data   = Signal(width)
        self.i_enable = Signal()
        self.i_clear  = Signal()

        self.o_get    = Signal()
        self.o_empty  = Signal()

        self.o_data   = Signal()

        shifter = Signal(self._width)
        pos = Signal(self._width, reset=0b1)

        empty = Signal()

        self.sync.usb += [
            If(self.i_enable,
                pos.eq(pos >> 1),
                shifter.eq(shifter >> 1),
                self.o_get.eq(empty),

                If(empty,
                    shifter.eq(self.i_data),
                    pos.eq(1 << (self._width-1)),
                )
            )
        ]

        self.sync.usb += [
            If(self.i_clear,
                shifter.eq(0),
                pos.eq(1)
            )
        ]

        self.comb += [
            empty.eq(pos[0]),
            self.o_empty.eq(empty),
            self.o_data.eq(shifter[0]),
        ]



class TxNRZIEncoder(Module):
    """
    NRZI Encode

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
    i_valid : Signal()
        Qualifies oe, data, and se0.

    i_oe : Signal()
        Indicates that the transmit pipeline should be driving USB.

    i_data : Signal()
        Data bit to be transmitted on USB. Qualified by o_valid.

    i_se0 : Signal()
        Overrides value of o_data when asserted and indicates that SE0 state
        should be asserted on USB. Qualified by o_valid.

    Output Ports
    ------------
    o_usbp : Signal()
        Raw value of USB+ line.

    o_usbn : Signal()
        Raw value of USB- line.

    o_oe : Signal()
        When asserted it indicates that the tx pipeline should be driving USB.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_oe = Signal()
        self.i_data = Signal()

        # flop all outputs
        self.o_usbp = Signal()
        self.o_usbn = Signal()
        self.o_oe = Signal()

        usbp = Signal()
        usbn = Signal()
        oe = Signal()

        # FSM state encoding
        fsm_state = Signal(3)
        STATE_IDLE = 0
        STATE_DJ   = 1
        STATE_DK   = 2
        STATE_SE0A = 3
        STATE_SE0B = 4
        STATE_EOPJ = 5

        # wait for new packet to start
        self.sync.usb_io += [
            If(fsm_state == STATE_IDLE,
                usbp.eq(1),
                usbn.eq(0),
                oe.eq(0),

                If(self.i_valid & self.i_oe,
                    # first bit of sync always forces a transition, we idle
                    # in J so the first output bit is K.
                    fsm_state.eq(STATE_DK)
                )
            ).Elif(fsm_state == STATE_DJ,
                usbp.eq(1),
                usbn.eq(0),
                oe.eq(1),

                If(self.i_valid,
                    If(~self.i_oe,
                        fsm_state.eq(STATE_SE0A)
                    ).Elif(self.i_data,
                        fsm_state.eq(STATE_DJ)
                    ).Else(
                        fsm_state.eq(STATE_DK)
                    )
                )
            ).Elif(fsm_state == STATE_DK,
                usbp.eq(0),
                usbn.eq(1),
                oe.eq(1),

                If(self.i_valid,
                    If(~self.i_oe,
                        fsm_state.eq(STATE_SE0A)
                    ).Elif(self.i_data,
                        fsm_state.eq(STATE_DK)
                    ).Else(
                        fsm_state.eq(STATE_DJ)
                    )
                )
            ).Elif(fsm_state == STATE_SE0A,
                usbp.eq(0),
                usbn.eq(0),
                oe.eq(1),

                If(self.i_valid,
                    fsm_state.eq(STATE_SE0B)
                )
            ).Elif(fsm_state == STATE_SE0B,
                usbp.eq(0),
                usbn.eq(0),
                oe.eq(1),

                If(self.i_valid,
                    fsm_state.eq(STATE_EOPJ)
                )
            ).Elif(fsm_state == STATE_EOPJ,
                usbp.eq(1),
                usbn.eq(0),
                oe.eq(1),

                If(self.i_valid,
                    fsm_state.eq(STATE_IDLE)
                )
            )
        ]

        self.sync.usb_io += [
            self.o_oe.eq(oe),
            self.o_usbp.eq(usbp),
            self.o_usbn.eq(usbn),
        ]


class TxBitstuffer(Module):
    """
    Bitstuff Insertion

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.

    The TxBitstuffer is the only component in the transmit pipeline that can
    delay transmission of serial data.  It is therefore responsible for
    generating the bit_strobe signal that keeps the pipe moving forward.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

    Clock Domain
    ------------
    usb_12 : 48MHz

    Input Ports
    ------------
    i_data : Signal()
        Data bit to be transmitted on USB.

    Output Ports
    ------------
    o_data : Signal()
        Data bit to be transmitted on USB.

    o_stall : Signal()
        Used to apply backpressure on the tx pipeline.
    """
    def __init__(self):
        self.i_data = Signal()

        self.o_stall = Signal()
        self.o_will_stall = Signal()
        self.o_data = Signal()

        stuff_bit = Signal()

        # FSM state encoding
        fsm_state = Signal(3)
        STATE_D0 = 0
        STATE_D1 = 1
        STATE_D2 = 2
        STATE_D3 = 3
        STATE_D4 = 4
        STATE_D5 = 5
        STATE_D6 = 6

        self.sync.usb += [
            If(fsm_state == STATE_D0,
                # Receiving '1' increments the bitstuff counter.
                If(self.i_data,
                    fsm_state.eq(STATE_D1)
                # Receiving '0' resets the bitstuff counter.
                ).Else(
                    fsm_state.eq(STATE_D0)
                )
            ).Elif(fsm_state == STATE_D1,
                If(self.i_data,
                    fsm_state.eq(STATE_D2)
                ).Else(
                    fsm_state.eq(STATE_D0)
                )
            ).Elif(fsm_state == STATE_D2,
                If(self.i_data,
                    fsm_state.eq(STATE_D3)
                ).Else(
                    fsm_state.eq(STATE_D0)
                )
            ).Elif(fsm_state == STATE_D3,
                If(self.i_data,
                    fsm_state.eq(STATE_D4)
                ).Else(
                    fsm_state.eq(STATE_D0)
                )
            ).Elif(fsm_state == STATE_D4,
                If(self.i_data,
                    fsm_state.eq(STATE_D5)
                ).Else(
                    fsm_state.eq(STATE_D0)
                )
            ).Elif(fsm_state == STATE_D5,
                If(self.i_data,
                    # There's a '1', so indicate we might stall on the next loop.
                    fsm_state.eq(STATE_D6)
                ).Else(
                    fsm_state.eq(STATE_D0)
                )
            ).Elif(fsm_state == STATE_D6,
                fsm_state.eq(STATE_D0)
            )
        ]

        self.comb += [
            stuff_bit.eq(fsm_state == STATE_D6),
            self.o_stall.eq(stuff_bit),
            self.o_will_stall.eq(fsm_state == STATE_D5),
        ]

        # flop outputs
        self.sync.usb += [
            If(stuff_bit,
                self.o_data.eq(0)
            ).Else(
                self.o_data.eq(self.i_data)
            )
        ]


class TxPipeline(Module):
    def __init__(self):
        self.i_bit_strobe = Signal()

        self.i_data_payload = Signal(8)
        self.o_data_strobe = Signal()

        self.i_oe = Signal()

        self.o_usbp = Signal()
        self.o_usbn = Signal()
        self.o_oe = Signal()

        self.o_pkt_end = Signal()

        self.fit_dat = Signal()
        self.fit_oe  = Signal()

        sync_pulse = Signal(8)

        da_reset_shifter = Signal()
        da_reset_bitstuff = Signal() # Need to reset the bit stuffer 1 cycle after the shifter.
        stall = Signal()

        # These signals are set during the sync pulse
        sp_reset_bitstuff = Signal()
        sp_reset_shifter = Signal()
        sp_bit = Signal()
        sp_o_data_strobe = Signal()

        # 12MHz domain
        bitstuff_valid_data = Signal()

        # Keep a Gray counter around to smoothly transition between states
        state_gray = Signal(2)
        state_data = Signal()
        state_sync = Signal()


        #
        # Transmit gearing.
        #
        shifter = TxShifter(width=8)
        self.submodules.shifter = shifter
        self.comb += [
            shifter.i_data.eq(self.i_data_payload),

            shifter.i_enable.eq(~stall),
            shifter.i_clear.eq(da_reset_shifter | sp_reset_shifter)
        ]

        #
        # Bit-stuffing and NRZI.
        #
        bitstuff = TxBitstuffer()
        self.submodules.bitstuff = bitstuff

        nrzi = TxNRZIEncoder()
        self.submodules.nrzi = nrzi


        #
        # Transmit controller.
        #

        self.comb += [
            # Send a data strobe when we're two bits from the end of the sync pulse.
            # This is because the pipeline takes two bit times, and we want to ensure the pipeline
            # has spooled up enough by the time we're there.
            bitstuff.i_data.eq(shifter.o_data),

            stall.eq(bitstuff.o_stall),

            sp_bit.eq(sync_pulse[0]),
            sp_reset_bitstuff.eq(sync_pulse[0]),

            # The shifter has one clock cycle of latency, so reset it
            # one cycle before the end of the sync byte.
            sp_reset_shifter.eq(sync_pulse[1]),

            sp_o_data_strobe.eq(sync_pulse[5]),

            state_data.eq(state_gray[0] & state_gray[1]),
            state_sync.eq(state_gray[0] & ~state_gray[1]),

            self.fit_oe.eq(state_data | state_sync),
            self.fit_dat.eq((state_data & shifter.o_data & ~bitstuff.o_stall) | sp_bit),
            self.o_data_strobe.eq(state_data & shifter.o_get & ~stall & self.i_oe),
        ]

        # If we reset the shifter, then o_empty will go high on the next cycle.
        #

        self.sync.usb += [
            # If the shifter runs out of data, percolate the "reset" signal to the
            # shifter, and then down to the bitstuffer.
            # da_reset_shifter.eq(~stall & shifter.o_empty & ~da_stalled_reset),
            # da_stalled_reset.eq(da_reset_shifter),
            # da_reset_bitstuff.eq(~stall & da_reset_shifter),
            bitstuff_valid_data.eq(~stall & shifter.o_get & self.i_oe),
        ]


        # FSM state encoding for transmit controller
        fsm_state = Signal(3)
        STATE_IDLE = 0
        STATE_SEND_SYNC = 1
        STATE_SEND_DATA = 2
        STATE_STUFF_LAST_BIT = 3

        self.sync.usb += [
            If(fsm_state == STATE_IDLE,
                If(self.i_oe,
                    sync_pulse.eq(1 << 7),
                    state_gray.eq(0b01),
                    fsm_state.eq(STATE_SEND_SYNC)
                ).Else(
                    state_gray.eq(0b00)
                )
            ).Elif(fsm_state == STATE_SEND_SYNC,
                sync_pulse.eq(sync_pulse >> 1),

                If(sync_pulse[0],
                    state_gray.eq(0b11),
                    fsm_state.eq(STATE_SEND_DATA)
                ).Else(
                    state_gray.eq(0b01)
                )
            ).Elif(fsm_state == STATE_SEND_DATA,
                If(~self.i_oe & shifter.o_empty & ~bitstuff.o_stall,
                    If(bitstuff.o_will_stall,
                        fsm_state.eq(STATE_STUFF_LAST_BIT)
                    ).Else(
                        state_gray.eq(0b10),
                        fsm_state.eq(STATE_IDLE)
                    )
                ).Else(
                    state_gray.eq(0b11)
                )
            ).Elif(fsm_state == STATE_STUFF_LAST_BIT,
                state_gray.eq(0b10),
                fsm_state.eq(STATE_IDLE)
            )
        ]


        # 48MHz domain
        # NRZI encoding
        nrzi_dat = Signal()
        nrzi_oe = Signal()

        # Cross the data from the 12MHz domain to the 48MHz domain
        cdc_dat = MultiReg(self.fit_dat, nrzi_dat, odomain="usb_io")
        cdc_oe  = MultiReg(self.fit_oe, nrzi_oe, odomain="usb_io")
        self.submodules += [cdc_dat, cdc_oe]

        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(nrzi_dat),
            nrzi.i_oe.eq(nrzi_oe),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),

        ]
