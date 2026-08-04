#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI register file tests — validates EHCI-spec-mandated register behavior.

Tests cover:
  - EHCI Spec Rev 1.0, Section 2.1 (Capability Registers)
      CAPLENGTH/HCIVERSION at 0x00, HCSPARAMS, HCCPARAMS
  - EHCI Spec Rev 1.0, Section 2.2 (Operational Registers at +CAPLENGTH)
      USBCMD / USBSTS (RW1C) / USBINTR / FRINDEX / CTRLDSSEGMENT /
      PERIODICLISTBASE / ASYNCLISTADDR / CONFIGFLAG / PORTSC
  - Interrupt generation: sticky status & enable = irq
  - Linux ehci_def.h register semantics (offsets, RW1C bits, HCHalted)

Wishbone addressing: bus.adr is word-indexed (byte offset >> 2), per the
LiteX Wishbone convention used when the slave is mapped into a SoC.
"""

from liteusb.tests.test_case import LiteUSBTestCase, sync_test_case

from liteusb.gateware.usb.usb2.host.registers       import EHCIRegisterFile
from liteusb.gateware.usb.usb2.host.data_structures import EHCIRegisters as REG


class EHCIRegisterFileTest(LiteUSBTestCase):
    """ Test the EHCI register file through its Wishbone slave. """

    FRAGMENT_UNDER_TEST = EHCIRegisterFile
    FRAGMENT_ARGUMENTS  = {"num_ports": 1}

    SYNC_CLOCK_FREQUENCY = 60e6
    USB_CLOCK_FREQUENCY  = None

    #
    # Wishbone helpers (addresses in bytes, converted to word index)
    #
    def wb_write(self, byte_addr, data):
        """ Perform a Wishbone write cycle. """
        yield self.dut.bus.adr.eq(byte_addr >> 2)
        yield self.dut.bus.dat_w.eq(data)
        yield self.dut.bus.we.eq(1)
        yield self.dut.bus.stb.eq(1)
        yield self.dut.bus.cyc.eq(1)
        yield
        yield self.dut.bus.stb.eq(0)
        yield self.dut.bus.cyc.eq(0)
        yield self.dut.bus.we.eq(0)
        yield
        yield

    def wb_read(self, byte_addr):
        """ Perform a Wishbone read cycle and return dat_r. """
        yield self.dut.bus.adr.eq(byte_addr >> 2)
        yield self.dut.bus.we.eq(0)
        yield self.dut.bus.stb.eq(1)
        yield self.dut.bus.cyc.eq(1)
        yield
        result = (yield self.dut.bus.dat_r)
        yield self.dut.bus.stb.eq(0)
        yield self.dut.bus.cyc.eq(0)
        yield
        return result

    # Operational register byte offsets (absolute, capability base = 0)
    USBCMD   = REG.OP_BASE + REG.OFF_USBCMD
    USBSTS   = REG.OP_BASE + REG.OFF_USBSTS
    USBINTR  = REG.OP_BASE + REG.OFF_USBINTR
    FRINDEX  = REG.OP_BASE + REG.OFF_FRINDEX
    CTRLDS   = REG.OP_BASE + REG.OFF_CTRLDSSEGMENT
    PLBASE   = REG.OP_BASE + REG.OFF_PERIODICLISTBASE
    ASYNCL   = REG.OP_BASE + REG.OFF_ASYNCLISTADDR
    CFGFLAG  = REG.OP_BASE + REG.OFF_CONFIGFLAG
    PORTSC1  = REG.OP_BASE + REG.OFF_PORTSC_BASE

    #
    # Capability registers (EHCI §2.1)
    #
    @sync_test_case
    def test_caplength_hciversion(self):
        """ Offset 0x00: CAPLENGTH=0x20 in bits 7:0, HCIVERSION=0x0100 in 31:16. """
        val = yield from self.wb_read(0x00)
        self.assertEqual(val & 0xFF, 0x20)
        self.assertEqual((val >> 16) & 0xFFFF, 0x0100)

    @sync_test_case
    def test_hcsparams(self):
        """ HCSPARAMS: N_PORTS=1, PPC=1, no companion controllers. """
        val = yield from self.wb_read(0x04)
        self.assertEqual(val & 0xF, 1)          # N_PORTS
        self.assertEqual((val >> 4) & 1, 1)     # PPC
        self.assertEqual((val >> 8) & 0xF, 0)   # N_CC = 0

    @sync_test_case
    def test_hccparams(self):
        """ HCCPARAMS: no 64-bit, no programmable frame list, no park. """
        val = yield from self.wb_read(0x08)
        self.assertEqual(val, 0)

    @sync_test_case
    def test_capability_registers_read_only(self):
        """ Writes to capability registers have no effect. """
        yield from self.wb_write(0x00, 0xFFFFFFFF)
        yield from self.wb_write(0x04, 0xFFFFFFFF)
        val = yield from self.wb_read(0x00)
        self.assertEqual(val & 0xFF, 0x20)

    #
    # USBCMD (EHCI §2.2.1)
    #
    @sync_test_case
    def test_usbcmd_run(self):
        """ USBCMD[0] (Run/Stop) sets the run output. """
        dut = self.dut
        self.assertEqual((yield dut.run), 0)

        yield from self.wb_write(self.USBCMD, REG.USBCMD_RUN)
        self.assertEqual((yield dut.run), 1)
        val = yield from self.wb_read(self.USBCMD)
        self.assertEqual(val & 1, 1)

        yield from self.wb_write(self.USBCMD, 0)
        self.assertEqual((yield dut.run), 0)

    @sync_test_case
    def test_usbcmd_hcreset_self_clearing(self):
        """ HCRESET is set by software and cleared by hardware. """
        dut = self.dut
        yield from self.wb_write(self.USBCMD, REG.USBCMD_HCRESET)
        self.assertEqual((yield dut.hc_reset), 1)

        # Hardware finishes the reset: bit must clear
        yield dut.hc_reset_done.eq(1)
        yield
        yield dut.hc_reset_done.eq(0)
        yield
        self.assertEqual((yield dut.hc_reset), 0)
        val = yield from self.wb_read(self.USBCMD)
        self.assertEqual(val & REG.USBCMD_HCRESET, 0)

    @sync_test_case
    def test_usbcmd_schedule_enables(self):
        """ USBCMD[4] (PSE) and USBCMD[5] (ASE) propagate. """
        dut = self.dut
        yield from self.wb_write(self.USBCMD, REG.USBCMD_PSE | REG.USBCMD_ASE)
        self.assertEqual((yield dut.periodic_enable), 1)
        self.assertEqual((yield dut.async_enable), 1)
        val = yield from self.wb_read(self.USBCMD)
        self.assertEqual(val & (REG.USBCMD_PSE | REG.USBCMD_ASE),
                         REG.USBCMD_PSE | REG.USBCMD_ASE)

    #
    # USBSTS (EHCI §2.2.2)
    #
    @sync_test_case
    def test_usbsts_sticky_set_and_w1c(self):
        """ Status bits latch on hardware strobes and clear on write-1. """
        dut = self.dut

        # Hardware strobes the USBINT event
        yield dut.usb_interrupt.eq(1)
        yield
        yield dut.usb_interrupt.eq(0)
        yield

        # Bit stays latched after the strobe is gone
        sts = yield from self.wb_read(self.USBSTS)
        self.assertEqual(sts & REG.USBSTS_USBINT, REG.USBSTS_USBINT)

        # Write 1 to clear
        yield from self.wb_write(self.USBSTS, REG.USBSTS_USBINT)
        sts = yield from self.wb_read(self.USBSTS)
        self.assertEqual(sts & REG.USBSTS_USBINT, 0)

    @sync_test_case
    def test_usbsts_write_zero_keeps_bit(self):
        """ Writing 0 to a status bit does NOT clear it (RW1C semantics). """
        dut = self.dut
        yield dut.usb_error.eq(1)
        yield
        yield dut.usb_error.eq(0)
        yield from self.wb_write(self.USBSTS, 0)
        sts = yield from self.wb_read(self.USBSTS)
        self.assertEqual(sts & REG.USBSTS_ERROR, REG.USBSTS_ERROR)

    @sync_test_case
    def test_usbsts_hchalted_read_only(self):
        """ HCHalted (bit 12) reflects the hardware input, is not W1C-able. """
        dut = self.dut
        self.assertEqual((yield dut.hc_halted), 1)
        sts = yield from self.wb_read(self.USBSTS)
        self.assertEqual(sts & REG.USBSTS_HCH, REG.USBSTS_HCH)

        # W1C write with bit 12 set must not disturb it
        yield from self.wb_write(self.USBSTS, 0xFFFFFFFF)
        sts = yield from self.wb_read(self.USBSTS)
        self.assertEqual(sts & REG.USBSTS_HCH, REG.USBSTS_HCH)

        yield dut.hc_halted.eq(0)
        yield
        sts = yield from self.wb_read(self.USBSTS)
        self.assertEqual(sts & REG.USBSTS_HCH, 0)

    #
    # USBINTR (EHCI §2.2.3) + interrupt generation
    #
    @sync_test_case
    def test_usbintr_masking(self):
        """ USBINTR only accepts the defined interrupt-enable bits. """
        yield from self.wb_write(self.USBINTR, 0xFFFFFFFF)
        val = yield from self.wb_read(self.USBINTR)
        self.assertEqual(val, 0x3F)

    @sync_test_case
    def test_interrupt_masked_by_default(self):
        """ No interrupt while the enable bit is clear, even with status set. """
        dut = self.dut
        yield dut.usb_interrupt.eq(1)
        yield
        yield dut.usb_interrupt.eq(0)
        yield
        self.assertEqual((yield dut.interrupt), 0)

    @sync_test_case
    def test_interrupt_generation(self):
        """ Interrupt asserts when sticky status & enable are both set. """
        dut = self.dut
        yield from self.wb_write(self.USBINTR, REG.USBINTR_USBINT)

        yield dut.usb_interrupt.eq(1)
        yield
        yield dut.usb_interrupt.eq(0)
        yield
        self.assertEqual((yield dut.interrupt), 1)

        # Clear the status: interrupt deasserts
        yield from self.wb_write(self.USBSTS, REG.USBSTS_USBINT)
        yield
        self.assertEqual((yield dut.interrupt), 0)

    @sync_test_case
    def test_interrupt_on_iaa(self):
        """ IAA status sets the interrupt and auto-clears the doorbell. """
        dut = self.dut
        yield from self.wb_write(self.USBCMD, REG.USBCMD_IAA)
        yield from self.wb_write(self.USBINTR, REG.USBINTR_IAA)
        self.assertEqual((yield dut.interrupt_on_aa), 1)

        yield dut.interrupt_on_aa_ack.eq(1)
        yield
        yield dut.interrupt_on_aa_ack.eq(0)
        yield
        self.assertEqual((yield dut.interrupt), 1)
        # Doorbell cleared by hardware (EHCI §2.2.1)
        self.assertEqual((yield dut.interrupt_on_aa), 0)

    #
    # FRINDEX (EHCI §2.2.4)
    #
    @sync_test_case
    def test_frindex_writable_when_halted(self):
        """ FRINDEX is writable while the HC is halted. """
        yield from self.wb_write(self.FRINDEX, 0x3FF8)
        val = yield from self.wb_read(self.FRINDEX)
        self.assertEqual(val, 0x3FF8)

    @sync_test_case
    def test_frindex_follows_hardware_when_running(self):
        """ While running, FRINDEX tracks the schedule engine's index. """
        dut = self.dut
        yield dut.hc_halted.eq(0)
        yield dut.frame_index_in.eq(0x1234)
        yield
        yield
        val = yield from self.wb_read(self.FRINDEX)
        self.assertEqual(val, 0x1234)

        # Software writes are ignored while running
        yield from self.wb_write(self.FRINDEX, 0)
        val = yield from self.wb_read(self.FRINDEX)
        self.assertEqual(val, 0x1234)

    #
    # Pointer registers (EHCI §2.2.5/§2.2.6)
    #
    @sync_test_case
    def test_periodiclistbase_alignment(self):
        """ PERIODICLISTBASE masks to 4K-aligned address. """
        yield from self.wb_write(self.PLBASE, 0xDEADBEEF)
        self.assertEqual((yield self.dut.frame_list_base), 0xDEADB000)
        val = yield from self.wb_read(self.PLBASE)
        self.assertEqual(val, 0xDEADB000)

    @sync_test_case
    def test_asynclistaddr_alignment(self):
        """ ASYNCLISTADDR masks to 32-byte-aligned address. """
        yield from self.wb_write(self.ASYNCL, 0xCAFEBABE)
        self.assertEqual((yield self.dut.async_list_addr), 0xCAFEBAA0)
        val = yield from self.wb_read(self.ASYNCL)
        self.assertEqual(val, 0xCAFEBAA0)

    @sync_test_case
    def test_ctrldssegment_read_write(self):
        """ CTRLDSSEGMENT is a plain 32-bit RW register. """
        yield from self.wb_write(self.CTRLDS, 0x12345678)
        val = yield from self.wb_read(self.CTRLDS)
        self.assertEqual(val, 0x12345678)

    #
    # CONFIGFLAG (EHCI §2.2.8)
    #
    @sync_test_case
    def test_configflag(self):
        """ CONFIGFLAG only bit 0 is writable. """
        self.assertEqual((yield self.dut.configure_flag), 0)
        yield from self.wb_write(self.CFGFLAG, 0xFFFFFFFF)
        self.assertEqual((yield self.dut.configure_flag), 1)
        val = yield from self.wb_read(self.CFGFLAG)
        self.assertEqual(val, 1)

    #
    # PORTSC (EHCI §2.2.9)
    #
    @sync_test_case
    def test_portsc_connect_status_read_only(self):
        """ CCS reflects the live input; software cannot change it. """
        dut = self.dut
        yield dut.port_connect[0].eq(1)
        yield
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_CCS, REG.PORTSC_CCS)

        yield from self.wb_write(self.PORTSC1, 0)
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_CCS, REG.PORTSC_CCS)

    @sync_test_case
    def test_portsc_csc_w1c(self):
        """ CSC is set by a connect-change strobe, cleared by write-1. """
        dut = self.dut
        yield dut.port_connect[0].eq(1)
        yield dut.port_connect_change[0].eq(1)
        yield
        yield dut.port_connect_change[0].eq(0)
        yield

        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_CSC, REG.PORTSC_CSC)

        yield from self.wb_write(self.PORTSC1, REG.PORTSC_CSC)
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_CSC, 0)

    @sync_test_case
    def test_portsc_pe_set_by_hw_cleared_by_sw(self):
        """ PE is set by hardware (reset complete), cleared by SW write of 0. """
        dut = self.dut
        yield dut.port_connect[0].eq(1)
        yield dut.port_enable[0].eq(1)
        yield
        yield dut.port_enable[0].eq(0)
        yield
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PE, REG.PORTSC_PE)

        # Software writes 0 to PE (keeping other RW bits 0, W1C bits 0)
        yield from self.wb_write(self.PORTSC1, 0)
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PE, 0)

    @sync_test_case
    def test_portsc_disconnect_clears_pe(self):
        """ PE clears when the device disconnects. """
        dut = self.dut
        yield dut.port_connect[0].eq(1)
        yield dut.port_enable[0].eq(1)
        yield
        yield dut.port_enable[0].eq(0)
        yield
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PE, REG.PORTSC_PE)

        yield dut.port_connect[0].eq(0)
        yield
        yield
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PE, 0)

    @sync_test_case
    def test_portsc_pr_drives_reset_output(self):
        """ Writing PORTSC.PR asserts the port_reset output. """
        dut = self.dut
        yield from self.wb_write(self.PORTSC1, REG.PORTSC_PR)
        self.assertEqual((yield dut.port_reset[0]), 1)
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PR, REG.PORTSC_PR)

        yield from self.wb_write(self.PORTSC1, 0)
        self.assertEqual((yield dut.port_reset[0]), 0)

    @sync_test_case
    def test_portsc_port_power_default_on(self):
        """ PP resets to 1 (ports powered), per EHCI §2.2.9. """
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PP, REG.PORTSC_PP)

    @sync_test_case
    def test_portsc_line_status_read_only(self):
        """ Line status bits [11:10] reflect the PHY. """
        dut = self.dut
        # EHCI encoding (post UTMI mapping, done in ehci.py): 01=K, 10=J
        yield dut.port_line_status[0:2].eq(0b01)  # K
        yield
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_LINE_STATUS_MASK,
                         REG.PORTSC_LINE_STATUS_K)

        yield dut.port_line_status[0:2].eq(0b10)  # J
        yield
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_LINE_STATUS_MASK,
                         REG.PORTSC_LINE_STATUS_J)

    @sync_test_case
    def test_portsc_port_owner(self):
        """ PORTSC[13] (Port Owner) is read/write. """
        dut = self.dut
        self.assertEqual((yield dut.port_owner[0]), 0)
        yield from self.wb_write(self.PORTSC1, REG.PORTSC_PO)
        self.assertEqual((yield dut.port_owner[0]), 1)
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & REG.PORTSC_PO, REG.PORTSC_PO)

    @sync_test_case
    def test_portsc_wake_bits(self):
        """ Wake-enable bits WKCNNT_E/WKDSCNNT_E/WKOC_E are RW. """
        dut = self.dut
        yield from self.wb_write(self.PORTSC1,
            REG.PORTSC_WKCNNT_E | REG.PORTSC_WKDSCNNT_E | REG.PORTSC_WKOC_E)
        self.assertEqual((yield dut.wake_on_connect[0]), 1)
        self.assertEqual((yield dut.wake_on_disconnect[0]), 1)
        self.assertEqual((yield dut.wake_on_overcurrent[0]), 1)
        val = yield from self.wb_read(self.PORTSC1)
        self.assertEqual(val & (REG.PORTSC_WKCNNT_E | REG.PORTSC_WKDSCNNT_E |
                                REG.PORTSC_WKOC_E),
                         REG.PORTSC_WKCNNT_E | REG.PORTSC_WKDSCNNT_E |
                         REG.PORTSC_WKOC_E)


class EHCIRegisterFileMultiPortTest(LiteUSBTestCase):
    """ PORTSC registers: one per port, 4-byte stride. """

    FRAGMENT_UNDER_TEST = EHCIRegisterFile
    FRAGMENT_ARGUMENTS  = {"num_ports": 2}

    SYNC_CLOCK_FREQUENCY = 60e6
    USB_CLOCK_FREQUENCY  = None

    def wb_write(self, byte_addr, data):
        yield self.dut.bus.adr.eq(byte_addr >> 2)
        yield self.dut.bus.dat_w.eq(data)
        yield self.dut.bus.we.eq(1)
        yield self.dut.bus.stb.eq(1)
        yield self.dut.bus.cyc.eq(1)
        yield
        yield self.dut.bus.stb.eq(0)
        yield self.dut.bus.cyc.eq(0)
        yield self.dut.bus.we.eq(0)
        yield
        yield

    def wb_read(self, byte_addr):
        yield self.dut.bus.adr.eq(byte_addr >> 2)
        yield self.dut.bus.we.eq(0)
        yield self.dut.bus.stb.eq(1)
        yield self.dut.bus.cyc.eq(1)
        yield
        result = (yield self.dut.bus.dat_r)
        yield self.dut.bus.stb.eq(0)
        yield self.dut.bus.cyc.eq(0)
        yield
        return result

    @sync_test_case
    def test_hcsparams_two_ports(self):
        """ HCSPARAMS reports N_PORTS=2. """
        val = yield from self.wb_read(0x04)
        self.assertEqual(val & 0xF, 2)

    @sync_test_case
    def test_portsc_independent_per_port(self):
        """ PORTSC1 and PORTSC2 are independent registers. """
        dut = self.dut
        portsc1 = REG.OP_BASE + REG.OFF_PORTSC_BASE
        portsc2 = portsc1 + REG.OFF_PORTSC_STRIDE

        yield dut.port_connect[0].eq(1)
        yield dut.port_connect[1].eq(0)
        yield

        val1 = yield from self.wb_read(portsc1)
        val2 = yield from self.wb_read(portsc2)
        self.assertEqual(val1 & REG.PORTSC_CCS, REG.PORTSC_CCS)
        self.assertEqual(val2 & REG.PORTSC_CCS, 0)

        # PR on port 2 only
        yield from self.wb_write(portsc2, REG.PORTSC_PR)
        self.assertEqual((yield dut.port_reset[0]), 0)
        self.assertEqual((yield dut.port_reset[1]), 1)
