#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" EHCI USB 2.0 Host Controller — top-level integration module.

Wraps the liteusb USB2 packet layer, PHY interfaces, and the newly-built
EHCI host controller components into a unified USBHostController module
suitable for LiteX SoC integration.

Architecture
------------
The EHCI host controller reuses the liteusb device-stack building blocks:

    LiteUSB Device Stack (reused)          EHCI Host Components (new)
    ─────────────────────────────          ──────────────────────────
    ULPIInterface / UTMITranslator         USBHostTokenGenerator
    USBDataPacketCRC                       USBHostTransferEngine
    USBHandshakeDetector                   EHCIScheduleProcessor
    USBDataPacketReceiver                  EHCIRegisterFile
    USBDataPacketGenerator                 USBHostController (this module)
    USBInterpacketTimer
    StreamInterface
    USBPacketID, USBSpeed, etc.

The host controller operates by:
1. Receiving a ULPI (or UTMI) PHY connection
2. Configuring the PHY for host mode (op_mode, term_select)
3. Running the SOF counter at 125µs microframe intervals
4. On each microframe: processing the periodic schedule, then async
5. Executing transfers via the token generator + data path
6. Exposing EHCI registers via Wishbone for software control

Usage
-----
    from liteusb.gateware.usb.usb2.host import USBHostController

    # With a ULPI PHY:
    ulpi = platform.request("ulpi")
    host = USBHostController(ulpi=ulpi)

    # Connect Wishbone:
    self.submodules.ehci = host
    self.add_wb_slave(0xe0000000, host.bus)

    # Connect interrupt:
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
from ..reset import USBResetSequencer

from .token_generator import USBHostTokenGenerator, USBSOFCounter
from .transfer import USBHostTransferEngine, HostTransferRequest, HostTransferResponse
from .schedule import EHCIScheduleProcessor
from .registers import EHCIRegisterFile
from .data_structures import EHCIRegisters as REG


class USBHostController(Module):
    """ EHCI-compatible USB 2.0 Host Controller.

    Integrates the PHY interface, packet layer, token generation,
    transfer execution, schedule processing, and EHCI register file
    into a single LiteX module.

    Parameters
    ----------
    bus : ULPIInterface, UTMIInterface, or raw I/O pads
        The PHY connection. ULPI is auto-detected and translated to UTMI.
        GatewarePHY is used for raw I/O (full-speed only).
    handle_clocking : bool
        Automatically connect USB clock domain (default True).
    num_ports : int
        Number of downstream ports (default 1).
    domain_clock : float
        UTMI clock frequency in Hz (default 60e6 = 60 MHz for HS ULPI).
    register_tx_outputs : bool
        Register TX outputs for timing closure at 60 MHz ULPI.

    Interface
    ---------
    bus : wishbone.Interface
        Wishbone slave for EHCI operational register access.
    interrupt : Signal()
        Interrupt output (active high).
    frame_number : Signal(11) output
        Current USB frame number.
    microframe_number : Signal(3) output
        Current USB microframe number.
    speed : Signal(2) output
        Current operating speed.
    suspended : Signal() output
        Host controller suspended.
    reset_detected : Signal() output
        Root port reset detected.
    """

    def __init__(self, bus, handle_clocking=True, num_ports=1,
                 domain_clock=60e6, register_tx_outputs=False):
        self.handle_clocking = handle_clocking
        self._num_ports = num_ports
        self._domain_clock = domain_clock

        # ── PHY detection and UTMI setup ────────────────────────────────

        # ULPI → UTMI translation
        if hasattr(bus, 'dir'):
            utmi = UTMITranslator(ulpi=bus, handle_clocking=handle_clocking,
                                    register_outputs=register_tx_outputs)
            self.submodules.utmi_translator = utmi
            self.always_hs = True
            self.data_clock = 60e6
        elif hasattr(bus, 'rx_data'):
            # Native UTMI
            utmi = bus
            self.always_hs = True
            self.data_clock = domain_clock
        else:
            # Raw I/O — use GatewarePHY (FS only)
            from ....interface.gateware_phy import GatewarePHY
            utmi = GatewarePHY(io=bus)
            self.submodules.gateware_phy = utmi
            self.always_hs = False
            self.data_clock = 12e6

        self.utmi = utmi

        # ── I/O ─────────────────────────────────────────────────────────

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
        # ── Host-mode UTMI configuration ────────────────────────────────
        #

        # In host mode:
        #  - op_mode: 0 (normal) — but host doesn't use op_mode the same way
        #    Actually: host sets op_mode=0 and term_select per speed
        #  - xcvr_select: 0 (HS), 1 (FS), 2 (LS)
        #  - term_select: 1 for FS/LS, 0 for HS (host pulls down D+/D-)
        #  - dm_pulldown, dp_pulldown: both 1 in host mode
        #
        # The reset sequencer from the device stack drives these signals.
        # For a host, we set them statically for HS operation.

        self.comb += [
            utmi.op_mode.eq(UTMIOperatingMode.NORMAL),
            utmi.xcvr_select.eq(USBSpeed.HIGH),
            utmi.term_select.eq(UTMITerminationSelect.HS_NORMAL),
            utmi.suspend.eq(0),
            # Host: pull down both D+ and D- (15kΩ)
            utmi.dm_pulldown.eq(1),
            utmi.dp_pulldown.eq(1),
            # Host: drive VBUS (not directly in UTMI, but conceptually)
            utmi.chrg_vbus.eq(0),
            utmi.dischrg_vbus.eq(0),
            # Host: don't need session signals for embedded host
            utmi.use_external_vbus_indicator.eq(1),
        ]

        #
        # ── Packet-layer components (reused from device stack) ──────────
        #

        # Data packet CRC (shared)
        self.submodules.data_crc = data_crc = USBDataPacketCRC()

        # Data packet generator (OUT/SETUP)
        self.submodules.data_tx = data_tx = USBDataPacketGenerator()
        data_crc.add_interface(data_tx.crc)

        # Data packet receiver (IN)
        self.submodules.data_rx = data_rx = USBDataPacketReceiver(utmi=utmi)
        data_crc.add_interface(data_rx.data_crc)

        # Handshake detector (ACK/NAK/STALL/NYET from device)
        self.submodules.hs_detector = hs_det = USBHandshakeDetector(utmi=utmi)

        # Handshake generator (ACK for IN transfers)
        self.submodules.hs_generator = hs_gen = USBHandshakeGenerator()

        # Interpacket timer
        self.submodules.timer = timer = USBInterpacketTimer(
            domain_clock=self.data_clock, fs_only=not self.always_hs)

        self.comb += [
            # CRC data hookup
            data_crc.rx_data.eq(utmi.rx_data),
            data_crc.rx_valid.eq(utmi.rx_valid),
            data_crc.tx_valid.eq(data_tx.tx.valid & utmi.tx_ready),
            data_crc.tx_data.eq(data_tx.tx.data),

            # Timer connections
            timer.speed.eq(USBSpeed.HIGH if self.always_hs else USBSpeed.FULL),
        ]

        #
        # ── Host token generator ────────────────────────────────────────
        #

        self.submodules.token_gen = token_gen = USBHostTokenGenerator(
            utmi=utmi, domain_clock=self.data_clock)
        self.comb += token_gen.sof_enable.eq(1)

        #
        # ── Transfer engine ─────────────────────────────────────────────
        #

        self.submodules.xfer_engine = xfer = USBHostTransferEngine(utmi=utmi)

        # Connect token generator to transfer engine
        self.comb += [
            xfer.token_pid.eq(token_gen.token_pid),
            xfer.token_address.eq(token_gen.token_address),
            xfer.token_endpoint.eq(token_gen.token_endpoint),
            xfer.token_busy.eq(token_gen.token_busy),
            token_gen.issue_token.eq(xfer.token_issue),
            xfer.bus_granted.eq(1),  # Simplified: always grant
        ]

        #
        # ── Schedule processor ──────────────────────────────────────────
        #

        self.submodules.schedule = schedule = EHCIScheduleProcessor(
            num_ports=self._num_ports)

        # SOF from token generator drives schedule
        self.comb += [
            schedule.sof_strobe.eq(token_gen.sof_counter.issue_sof),
        ]

        #
        # ── Register file ───────────────────────────────────────────────
        #

        self.submodules.registers = regs = EHCIRegisterFile(
            num_ports=self._num_ports)

        # Connect Wishbone
        self.comb += self.bus.connect(regs.bus)

        # Connect register outputs to sub-modules
        self.comb += [
            schedule.run              .eq(regs.run),
            schedule.periodic_enable  .eq(regs.periodic_enable),
            schedule.async_enable     .eq(regs.async_enable),
            schedule.frame_list_base  .eq(regs.frame_list_base),
            schedule.async_list_addr  .eq(regs.async_list_addr),
            token_gen.sof_enable      .eq(regs.run),
        ]

        # Connect hardware status back to registers
        self.comb += [
            regs.hc_halted      .eq(schedule.hc_halted),
            regs.frame_index_in .eq(schedule.frame_index),
        ]

        #
        # ── Transmit multiplexer ────────────────────────────────────────
        #

        # Merge token generator and data/HS transmitter outputs onto UTMI
        self.submodules.tx_mux = tx_mux = UTMIInterfaceMultiplexer()

        # Each component that drives UTMI TX gets an input
        # Note: the token_generator drives utmi directly in its own module.
        # For the host, we need to multiplex:
        #   1. Token generator TX
        #   2. Data packet TX
        #   3. Handshake TX (host ACK for IN transfers)
        #
        # These share the UTMI bus; only one can transmit at a time.

        # We create interfaces for each transmitter
        # Token generator: already drives utmi directly, we need a proxy
        token_tx = UTMITransmitInterface()
        data_tx_if = UTMITransmitInterface()
        hs_tx_if = UTMITransmitInterface()

        tx_mux.add_input(token_tx)
        tx_mux.add_input(data_tx_if)
        tx_mux.add_input(hs_tx_if)

        self.comb += [
            # Token gen TX → mux input
            token_tx.valid.eq(utmi.tx_valid),
            token_tx.data.eq(utmi.tx_data),

            # Data packet TX → mux input
            data_tx_if.valid.eq(data_tx.tx.valid),
            data_tx_if.data.eq(data_tx.tx.data),

            # Handshake TX → mux input
            hs_tx_if.valid.eq(hs_gen.tx.valid),
            hs_tx_if.data.eq(hs_gen.tx.data),

            # Mux output → UTMI bus (physical)
            utmi.tx_valid.eq(tx_mux.output.valid),
            utmi.tx_data.eq(tx_mux.output.data),

            # Ready signal from UTMI to all transmitters
            token_tx.ready.eq(utmi.tx_ready),
            data_tx_if.ready.eq(utmi.tx_ready),
            hs_tx_if.ready.eq(utmi.tx_ready),
        ]

        #
        # ── Status outputs ──────────────────────────────────────────────
        #

        self.comb += [
            self.frame_number      .eq(token_gen.sof_counter.frame_number),
            self.microframe_number .eq(token_gen.sof_counter.microframe_number),
            self.speed             .eq(
                USBSpeed.HIGH if self.always_hs else USBSpeed.FULL),
            self.interrupt         .eq(regs.interrupt),
        ]

        #
        # ── Port status — read UTMI line_state ─────────────────────────
        #

        # Simple port status: detect device connect/disconnect from line_state
        line_state = utmi.line_state
        # UTMI line_state: 00=SE0, 01=J, 10=K, 11=SE1
        # Device connect is detected when D+ goes high (J state for FS/HS)

        port_connected     = Signal()
        port_enabled       = Signal()
        port_reset_active  = Signal()
        port_speed         = Signal(2)  # 0=HS, 1=FS, 2=LS

        self.sync.usb += [
            # Simple connect detection: J state means device attached
            If(line_state == 0b01,  # J state
                port_connected.eq(1),
            ).Elif(line_state == 0b00,  # SE0 = disconnect
                port_connected.eq(0),
                port_enabled.eq(0),
            )
        ]

        # Build PORTSC-like status word
        self.comb += [
            regs.port_status.eq(Cat(
                port_connected,      # bit 0: CCS
                Signal(),            # bit 1: CSC
                port_enabled,        # bit 2: PE
                Signal(),            # bit 3: PEC
                Signal(),            # bit 4: OCA
                Signal(),            # bit 5: OCC
                Signal(),            # bit 6: FPR
                Signal(),            # bit 7: SUSP
                port_reset_active,   # bit 8: PR
                Replicate(0, 23),    # bits 9-31 reserved
            )),
        ]
