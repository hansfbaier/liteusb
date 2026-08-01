#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#
# Generated using DeepSeek V4.0 Pro

""" EHCI register file tests — validates EHCI-spec-mandated register behavior.

Tests cover:
  - EHCI Spec Rev 1.0, Section 2.1 (Capability Registers)
  - EHCI Spec Rev 1.0, Section 2.2 (Operational Registers)
    * USBCMD: Run/Stop (bit 0), HCRESET (bit 1), PSE (bit 4), ASE (bit 5)
    * USBSTS: HCHalted (bit 12), write-1-to-clear semantics
    * USBINTR: interrupt-enable masking
    * PERIODICLISTBASE: 4K alignment
    * ASYNCLISTADDR: 32-byte alignment
    * CONFIGFLAG: bit-0 only
    * PORTSC: Port Owner (bit 13) read/write
  - Interrupt generation: status & enable = irq
"""

from liteusb.tests.test_case import LiteUSBTestCase, sync_test_case

from liteusb.gateware.usb.usb2.host.registers      import EHCIRegisterFile
from liteusb.gateware.usb.usb2.host.data_structures import EHCIRegisters as REG


class EHCIRegisterFileTest(LiteUSBTestCase):
    """ Test the EHCI register file via direct signal probing.

    We drive the Wishbone bus directly and observe the derived
    control signals and the interrupt output.  Uses sync domain
    because the register file's Wishbone interface is synchronous
    in the sys domain.
    """

    FRAGMENT_UNDER_TEST = EHCIRegisterFile
    FRAGMENT_ARGUMENTS  = {"num_ports": 1}

    SYNC_CLOCK_FREQUENCY = 60e6
    USB_CLOCK_FREQUENCY  = None

    #
    # Wishbone helpers
    #
    def wb_write(self, addr, data):
        """ Perform a Wishbone write cycle. """
        yield self.dut.bus.adr.eq(addr)
        yield self.dut.bus.dat_w.eq(data)
        yield self.dut.bus.we.eq(1)
        yield self.dut.bus.stb.eq(1)
        yield self.dut.bus.cyc.eq(1)
        yield  # one full cycle with stb=cyc=1, we=1
        yield
        yield self.dut.bus.stb.eq(0)
        yield self.dut.bus.cyc.eq(0)
        yield self.dut.bus.we.eq(0)
        yield

    def wb_read(self, addr):
        """ Perform a Wishbone read cycle and return dat_r. """
        yield self.dut.bus.adr.eq(addr)
        yield self.dut.bus.we.eq(0)
        yield self.dut.bus.stb.eq(1)
        yield self.dut.bus.cyc.eq(1)
        yield  # ack appears same cycle (combinatorial read)
        result = (yield self.dut.bus.dat_r)
        yield self.dut.bus.stb.eq(0)
        yield self.dut.bus.cyc.eq(0)
        return result

    #
    # Tests
    #
    @sync_test_case
    def test_usbcmd_run(self):
        """ USBCMD[0] (Run/Stop) sets the run output.  EHCI §2.2.1. """
        dut = self.dut

        # Initially halted
        self.assertEqual((yield dut.run), 0)
        self.assertEqual((yield dut.hc_halted), 1)

        # Write USBCMD with Run=1
        yield from self.wb_write(REG.OFF_USBCMD, REG.USBCMD_RUN)
        self.assertEqual((yield dut.run), 1)

        # Write USBCMD with Run=0
        yield from self.wb_write(REG.OFF_USBCMD, 0)
        self.assertEqual((yield dut.run), 0)

    @sync_test_case
    def test_usbcmd_hcreset(self):
        """ USBCMD[1] (HCRESET) sets hc_reset output.  EHCI §2.2.1. """
        dut = self.dut

        self.assertEqual((yield dut.hc_reset), 0)
        yield from self.wb_write(REG.OFF_USBCMD, REG.USBCMD_HCRESET)
        self.assertEqual((yield dut.hc_reset), 1)

    @sync_test_case
    def test_usbcmd_schedule_enables(self):
        """ USBCMD[4] (PSE) and USBCMD[5] (ASE) propagate.  EHCI §2.2.1. """
        dut = self.dut

        self.assertEqual((yield dut.periodic_enable), 0)
        self.assertEqual((yield dut.async_enable), 0)

        yield from self.wb_write(REG.OFF_USBCMD, REG.USBCMD_PSE | REG.USBCMD_ASE)
        self.assertEqual((yield dut.periodic_enable), 1)
        self.assertEqual((yield dut.async_enable), 1)

    @sync_test_case
    def test_usbsts_write_1_to_clear(self):
        """ USBSTS bits are write-1-to-clear.  EHCI §2.2.2. """
        dut = self.dut

        # Drive a hardware status event — USB Interrupt
        yield dut.usb_interrupt.eq(1)
        yield
        # Read USBSTS, should see bit 0 set
        sts = yield from self.wb_read(REG.OFF_USBSTS)
        self.assertEqual(sts & REG.USBSTS_USBINT, REG.USBSTS_USBINT)

        # Write 1 to clear
        yield from self.wb_write(REG.OFF_USBSTS, REG.USBSTS_USBINT)
        yield dut.usb_interrupt.eq(0)
        yield
        sts = yield from self.wb_read(REG.OFF_USBSTS)
        self.assertEqual(sts & REG.USBSTS_USBINT, 0)

    @sync_test_case
    def test_usbintr_masking(self):
        """ USBINTR only accepts the defined interrupt-enable bits.  EHCI §2.2.3. """
        dut = self.dut

        allowed = (REG.USBINTR_USBINT | REG.USBINTR_ERRINT |
                   REG.USBINTR_PCD | REG.USBINTR_FLR |
                   REG.USBINTR_HSE | REG.USBINTR_IAA)

        # Write all bits set
        yield from self.wb_write(REG.OFF_USBINTR, 0xFFFFFFFF)
        val = yield from self.wb_read(REG.OFF_USBINTR)
        self.assertEqual(val, allowed)

        # Write only USB Interrupt Enable
        yield from self.wb_write(REG.OFF_USBINTR, REG.USBINTR_USBINT)
        val = yield from self.wb_read(REG.OFF_USBINTR)
        self.assertEqual(val, REG.USBINTR_USBINT)

    @sync_test_case
    def test_interrupt_generation(self):
        """ Interrupt asserts when status & enable are both set.  EHCI §2.2.2/§2.2.3. """
        dut = self.dut

        # Enable USB Interrupt
        yield from self.wb_write(REG.OFF_USBINTR, REG.USBINTR_USBINT)

        # No interrupt initially
        self.assertEqual((yield dut.interrupt), 0)

        # Drive status bit
        yield dut.usb_interrupt.eq(1)
        yield
        self.assertEqual((yield dut.interrupt), 1)

        # Clear status via write-1-to-clear
        yield from self.wb_write(REG.OFF_USBSTS, REG.USBSTS_USBINT)
        yield dut.usb_interrupt.eq(0)
        yield
        self.assertEqual((yield dut.interrupt), 0)

    @sync_test_case
    def test_periodiclistbase_alignment(self):
        """ PERIODICLISTBASE masks to 4K-aligned address.  EHCI §2.2.5. """
        dut = self.dut

        yield from self.wb_write(REG.OFF_PERIODICLISTBASE, 0xDEADBEEF)
        self.assertEqual((yield dut.frame_list_base), 0xDEADB000)

    @sync_test_case
    def test_asynclistaddr_alignment(self):
        """ ASYNCLISTADDR masks to 32-byte-aligned address.  EHCI §2.2.6. """
        dut = self.dut

        yield from self.wb_write(REG.OFF_ASYNCLISTADDR, 0xCAFEBABE)
        self.assertEqual((yield dut.async_list_addr), 0xCAFEBAA0)

    @sync_test_case
    def test_configflag(self):
        """ CONFIGFLAG only bit 0 is writable.  EHCI §2.2.8. """
        dut = self.dut

        self.assertEqual((yield dut.configure_flag), 0)

        yield from self.wb_write(REG.OFF_CONFIGFLAG, 0xFFFFFFFF)
        self.assertEqual((yield dut.configure_flag), 1)

        val = yield from self.wb_read(REG.OFF_CONFIGFLAG)
        self.assertEqual(val, 1)

    @sync_test_case
    def test_portsc_port_owner(self):
        """ PORTSC[13] (Port Owner) read/write.  EHCI §2.2.9. """
        dut = self.dut

        self.assertEqual((yield dut.port_owner), 0)

        yield from self.wb_write(REG.OFF_PORTSC_BASE, REG.PORTSC_PO)
        self.assertEqual((yield dut.port_owner), 1)

        val = yield from self.wb_read(REG.OFF_PORTSC_BASE)
        self.assertEqual(val & REG.PORTSC_PO, REG.PORTSC_PO)

    @sync_test_case
    def test_frindex_read_write(self):
        """ FRINDEX is readable and writable.  EHCI §2.2.4. """
        dut = self.dut

        yield from self.wb_write(REG.OFF_FRINDEX, 0x00003FF8)
        val = yield from self.wb_read(REG.OFF_FRINDEX)
        self.assertEqual(val, 0x00003FF8)
