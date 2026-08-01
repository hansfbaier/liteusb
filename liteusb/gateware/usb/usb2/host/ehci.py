#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" EHCI USB 2.0 Host Controller — top-level integration module.

Wraps the liteusb USB2 packet layer, PHY interfaces, and EHCI host
controller components into a unified USBHostController module suitable
for LiteX SoC integration. Supports High-Speed, Full-Speed, and
Low-Speed devices via an integrated Transaction Translator.

Architecture
------------
    LiteUSB Device Stack (reused)          EHCI Host Components (new)
    ─────────────────────────────          ──────────────────────────
    ULPIInterface / UTMITranslator         USBHostTokenGenerator
    USBDataPacketCRC                       USBHostTransferEngine
    USBHandshakeDetector                   EHCIScheduleProcessor
    USBDataPacketReceiver                  EHCIRegisterFile
    USBDataPacketGenerator                 HostResetSequencer
    USBInterpacketTimer                    TransactionTranslator
    StreamInterface                        USBHostController (this module)
    USBPacketID, USBSpeed, etc.

Usage
-----
    from liteusb.gateware.usb.usb2.host import USBHostController

    ulpi = platform.request("ulpi")
    host = USBHostController(ulpi=ulpi)

    self.submodules.ehci = host
    self.add_wb_slave(0xe0000000, host.bus)
    self.comb += self.cpu.interrupt.eq(host.interrupt)
"""

from migen import *

from litex.soc.interconnect import wishbone

from ....interface.ulpi import ULPIInterface, UTMITranslator
from ....interface.utmi import (
    UTMIInterface, UTMITransmitInterface,
    UTMIInterfaceMultiplexer, UTMIOperatingMode, UTMITerminationSelect,
)
from .. import USBSpeed, USBPacketID
from ..packet import (
    USBDataPacketGenerator, USBDataPacketReceiver,
    USBHandshakeDetector, USBHandshakeGenerator,
    USBDataPacketCRC, USBInterpacketTimer,
)

from .token_generator import USBHostTokenGenerator, USBSOFCounter
from .transfer import USBHostTransferEngine, HostTransferRequest, HostTransferResponse
from .schedule import EHCIScheduleProcessor
from .registers import EHCIRegisterFile
from .reset_host import HostResetSequencer
from .transaction_translator import TransactionTranslator
from .data_structures import EHCIRegisters as REG


class USBHostController(Module):
    """ EHCI-compatible USB 2.0 Host Controller with integrated TT.

    Supports HS (480 Mbps), FS (12 Mbps), and LS (1.5 Mbps) devices
    through an integrated Transaction Translator.

    Parameters
    ----------
    bus : ULPIInterface, UTMIInterface, or raw I/O pads
    handle_clocking : bool
    num_ports : int
    domain_clock : float
    register_tx_outputs : bool

    Interface
    ---------
    bus : wishbone.Interface
    interrupt : Signal()
    frame_number : Signal(11)
    microframe_number : Signal(3)
    speed : Signal(2)
    suspended : Signal()
    reset_detected : Signal()
    """

    def __init__(self, bus, handle_clocking=True, num_ports=1,
                 domain_clock=60e6, register_tx_outputs=False):
        self.handle_clocking = handle_clocking
        self._num_ports = num_ports
        self._domain_clock = domain_clock

        # PHY detection
        if hasattr(bus, 'dir'):
            utmi = UTMITranslator(ulpi=bus, handle_clocking=handle_clocking,
                                    register_outputs=register_tx_outputs)
            self.submodules.utmi_translator = utmi
            self.always_hs = True
            self.data_clock = 60e6
        elif hasattr(bus, 'rx_data'):
            utmi = bus
            self.always_hs = True
            self.data_clock = domain_clock
        else:
            from ....interface.gateware_phy import GatewarePHY
            utmi = GatewarePHY(io=bus)
            self.submodules.gateware_phy = utmi
            self.always_hs = False
            self.data_clock = 12e6

        self.utmi = utmi

        # I/O
        self.bus       = wishbone.Interface(data_width=32, address_width=8)
        self.interrupt = Signal()
        self.frame_number      = Signal(11)
        self.microframe_number = Signal(3)
        self.speed             = Signal(2)
        self.suspended         = Signal()
        self.reset_detected    = Signal()

    def do_finalize(self):
        utmi = self.utmi

        #
        # ── Port speed state ────────────────────────────────────────────
        #

        port_speed       = Signal(2, reset=USBSpeed.HIGH)
        port_speed_valid = Signal()

        #
        # ── Host Reset Sequencer ─────────────────────────────────────────
        #

        self.submodules.reset_seq = reset_seq = HostResetSequencer(
            domain_clock=self.data_clock)

        # Trigger reset on port connect detect or PORTSC.PR write
        do_port_reset = Signal()
        port_connected = Signal()

        # Simple connect detection: J state means device attached
        self.sync.usb += [
            If(utmi.line_state == 0b01,
                port_connected.eq(1),
                do_port_reset.eq(1),
            ).Elif(utmi.line_state == 0b00,
                port_connected.eq(0),
                port_speed_valid.eq(0),
            )
        ]

        self.comb += [
            reset_seq.line_state.eq(utmi.line_state),
            reset_seq.bus_busy.eq(0),
            reset_seq.start.eq(do_port_reset),
        ]

        self.sync.usb += [
            If(reset_seq.done,
                port_speed.eq(reset_seq.current_speed),
                port_speed_valid.eq(1),
                do_port_reset.eq(0),
            )
        ]

        #
        # ── Integrated Transaction Translator ───────────────────────────
        #

        self.submodules.tt = tt = TransactionTranslator()
        self.comb += [
            tt.utmi_line_state.eq(utmi.line_state),
            tt.utmi_rx_data.eq(utmi.rx_data),
            tt.utmi_rx_valid.eq(utmi.rx_valid),
            tt.utmi_rx_active.eq(utmi.rx_active),
        ]

        #
        # ── UTMI configuration (speed-dependent, TT-aware) ──────────────
        #

        self.comb += [
            If(tt.busy,
                utmi.op_mode.eq(tt.utmi_op_mode),
                utmi.xcvr_select.eq(tt.utmi_xcvr_select),
                utmi.term_select.eq(tt.utmi_term_select),
            ).Elif(do_port_reset,
                utmi.op_mode.eq(reset_seq.op_mode),
                utmi.xcvr_select.eq(reset_seq.xcvr_select),
                utmi.term_select.eq(reset_seq.term_select),
            ).Else(
                utmi.op_mode.eq(UTMIOperatingMode.NORMAL),
                utmi.xcvr_select.eq(port_speed),
                utmi.term_select.eq(
                    UTMITerminationSelect.HS_NORMAL
                    if port_speed == USBSpeed.HIGH
                    else UTMITerminationSelect.LS_FS_NORMAL),
            ),
            utmi.dm_pulldown.eq(1),
            utmi.dp_pulldown.eq(1),
            utmi.suspend.eq(0),
            utmi.chrg_vbus.eq(0),
            utmi.dischrg_vbus.eq(0),
            utmi.use_external_vbus_indicator.eq(1),
        ]

        #
        # ── Packet-layer components ─────────────────────────────────────
        #

        self.submodules.data_crc = data_crc = USBDataPacketCRC()
        self.submodules.data_tx = data_tx = USBDataPacketGenerator()
        data_crc.add_interface(data_tx.crc)
        self.submodules.data_rx = data_rx = USBDataPacketReceiver(utmi=utmi)
        data_crc.add_interface(data_rx.data_crc)
        self.submodules.hs_detector = hs_det = USBHandshakeDetector(utmi=utmi)
        self.submodules.hs_generator = hs_gen = USBHandshakeGenerator()
        self.submodules.timer = timer = USBInterpacketTimer(
            domain_clock=self.data_clock, fs_only=not self.always_hs)

        self.comb += [
            data_crc.rx_data.eq(utmi.rx_data),
            data_crc.rx_valid.eq(utmi.rx_valid),
            data_crc.tx_valid.eq(data_tx.tx.valid & utmi.tx_ready),
            data_crc.tx_data.eq(data_tx.tx.data),
            timer.speed.eq(port_speed),
        ]

        #
        # ── Host token generator ────────────────────────────────────────
        #

        self.submodules.token_gen = token_gen = USBHostTokenGenerator(
            utmi=utmi, domain_clock=self.data_clock)
        self.comb += [
            token_gen.sof_enable.eq(1),
            token_gen.sof_counter.speed.eq(port_speed),
            token_gen.sof_counter.sof_hold.eq(tt.sof_hold),
        ]

        #
        # ── Transfer engine ─────────────────────────────────────────────
        #

        self.submodules.xfer_engine = xfer = USBHostTransferEngine(utmi=utmi)
        self.comb += [
            xfer.token_pid.eq(token_gen.token_pid),
            xfer.token_address.eq(token_gen.token_address),
            xfer.token_endpoint.eq(token_gen.token_endpoint),
            xfer.token_busy.eq(token_gen.token_busy),
            token_gen.issue_token.eq(xfer.token_issue),
            xfer.bus_granted.eq(1),
        ]

        #
        # ── Schedule processor ──────────────────────────────────────────
        #

        self.submodules.schedule = schedule = EHCIScheduleProcessor(
            num_ports=self._num_ports)
        self.comb += [
            schedule.sof_strobe.eq(token_gen.sof_counter.issue_sof),
            schedule.port_speed.eq(port_speed),
        ]

        #
        # ── Register file ───────────────────────────────────────────────
        #

        self.submodules.registers = regs = EHCIRegisterFile(
            num_ports=self._num_ports)
        self.comb += self.bus.connect(regs.bus)

        self.comb += [
            schedule.run              .eq(regs.run),
            schedule.periodic_enable  .eq(regs.periodic_enable),
            schedule.async_enable     .eq(regs.async_enable),
            schedule.frame_list_base  .eq(regs.frame_list_base),
            schedule.async_list_addr  .eq(regs.async_list_addr),
            token_gen.sof_enable      .eq(regs.run),
            regs.hc_halted            .eq(schedule.hc_halted),
            regs.frame_index_in       .eq(schedule.frame_index),
        ]

        #
        # ── Transmit multiplexer ────────────────────────────────────────
        #

        self.submodules.tx_mux = tx_mux = UTMIInterfaceMultiplexer()
        token_tx = UTMITransmitInterface()
        data_tx_if = UTMITransmitInterface()
        hs_tx_if = UTMITransmitInterface()
        tt_tx_if = UTMITransmitInterface()

        tx_mux.add_input(token_tx)
        tx_mux.add_input(data_tx_if)
        tx_mux.add_input(hs_tx_if)
        tx_mux.add_input(tt_tx_if)

        self.comb += [
            token_tx.valid.eq(utmi.tx_valid),
            token_tx.data.eq(utmi.tx_data),
            data_tx_if.valid.eq(data_tx.tx.valid),
            data_tx_if.data.eq(data_tx.tx.data),
            hs_tx_if.valid.eq(hs_gen.tx.valid),
            hs_tx_if.data.eq(hs_gen.tx.data),
            tt_tx_if.valid.eq(tt.utmi_tx_valid),
            tt_tx_if.data.eq(tt.utmi_tx_data),
            utmi.tx_valid.eq(tx_mux.output.valid),
            utmi.tx_data.eq(tx_mux.output.data),
            token_tx.ready.eq(utmi.tx_ready),
            data_tx_if.ready.eq(utmi.tx_ready),
            hs_tx_if.ready.eq(utmi.tx_ready),
            tt_tx_if.ready.eq(utmi.tx_ready),
        ]

        #
        # ── Status outputs ──────────────────────────────────────────────
        #

        self.comb += [
            self.frame_number      .eq(token_gen.sof_counter.frame_number),
            self.microframe_number .eq(token_gen.sof_counter.microframe_number),
            self.speed             .eq(port_speed),
            self.interrupt         .eq(regs.interrupt),
        ]

        #
        # ── PORTSC register status ──────────────────────────────────────
        #

        self.comb += [
            regs.port_status.eq(Cat(
                port_connected & port_speed_valid,  # bit 0: CCS
                Signal(),                            # bit 1: CSC
                port_speed_valid,                    # bit 2: PE
                Signal(),                            # bit 3: PEC
                Signal(),                            # bit 4: OCA
                Signal(),                            # bit 5: OCC
                Signal(),                            # bit 6: FPR
                Signal(),                            # bit 7: SUSP
                do_port_reset,                       # bit 8: PR
                Replicate(0, 23),                    # bits 9-31
            )),
        ]
