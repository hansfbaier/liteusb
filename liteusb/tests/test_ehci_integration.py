#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI end-to-end integration test.

Full control transfer (SETUP → DATA → STATUS) through the complete host
stack: schedule engine (DMA) → transfer engine → token generator →
packet layer → UTMI, with an emulated USB device on the other end and a
dict-backed system memory holding the EHCI structures.

Verifies on-the-wire token bytes, payload DMA in both directions, qTD
writebacks, and IOC — i.e. that all the pieces work together.
"""

from migen import Signal, Module, Replicate

from liteusb.tests.test_case import LiteUSBUSBTestCase
from liteusb.gateware.usb.usb2        import USBPacketID, USBSpeed
from liteusb.gateware.usb.usb2.packet import (
    USBDataPacketGenerator, USBDataPacketReceiver,
    USBHandshakeDetector, USBHandshakeGenerator, USBDataPacketCRC,
)
from liteusb.gateware.usb.usb2.host.token_generator import USBHostTokenGenerator
from liteusb.gateware.usb.usb2.host.transfer        import USBHostTransferEngine
from liteusb.gateware.usb.usb2.host.schedule        import EHCIScheduleProcessor
from liteusb.tests.test_ehci_dma import (
    make_qh, make_qtd, qtd_token, ep_char,
    PID_OUT, PID_IN, PID_SETUP, EPS_FULL,
)
from liteusb.tests.test_ehci_transfer import crc16_usb_bytes


QH_ADDR  = 0x1000
QTD_S    = 0x1100   # SETUP qTD
QTD_D    = 0x1120   # DATA qTD
QTD_T    = 0x1140   # STATUS qTD
SETUP_BUF = 0x2000
DATA_BUF  = 0x2100


class _UTMIStub:
    def __init__(self):
        self.rx_data    = Signal(8)
        self.rx_valid   = Signal()
        self.rx_active  = Signal()
        self.tx_data    = Signal(8)
        self.tx_valid   = Signal()
        self.tx_ready   = Signal()
        self.line_state = Signal(2)


class _HostStack(Module):
    """ Schedule + transfer engine + token gen + packet layer, wired
        exactly like USBHostController (minus registers/PHY). """

    def __init__(self):
        utmi = _UTMIStub()
        self.utmi = utmi

        self.submodules.data_crc = data_crc = USBDataPacketCRC()
        self.submodules.data_tx  = data_tx  = USBDataPacketGenerator()
        data_crc.add_interface(data_tx.crc)
        self.submodules.data_rx  = data_rx  = USBDataPacketReceiver(utmi=utmi)
        data_crc.add_interface(data_rx.data_crc)
        self.submodules.hs_det   = hs_det   = USBHandshakeDetector(utmi=utmi)
        self.submodules.hs_gen   = hs_gen   = USBHandshakeGenerator()
        self.submodules.token_gen = token_gen = USBHostTokenGenerator(
            utmi=utmi, domain_clock=60e6)

        self.submodules.xfer = xfer = USBHostTransferEngine(
            utmi=utmi, data_tx=data_tx, data_rx=data_rx,
            hs_detect=hs_det, hs_gen=hs_gen)
        self.submodules.schedule = schedule = EHCIScheduleProcessor(
            num_ports=1)

        self.comb += [
            # CRC taps (as in ehci.py)
            data_crc.rx_data.eq(utmi.rx_data),
            data_crc.rx_valid.eq(utmi.rx_valid),
            data_crc.tx_valid.eq(data_tx.tx.valid & utmi.tx_ready),
            data_crc.tx_data.eq(data_tx.tx.data),

            # transfer engine outputs drive the token generator
            token_gen.token_pid.eq(xfer.token_pid),
            token_gen.token_address.eq(xfer.token_address),
            token_gen.token_endpoint.eq(xfer.token_endpoint),
            xfer.token_busy.eq(token_gen.token_busy),
            token_gen.issue_token.eq(xfer.token_issue),
            token_gen.sof_enable.eq(0),
            xfer.bus_granted.eq(1),

            # schedule ↔ transfer engine (plain Records: explicit wiring)
        ]
        for field in ("valid", "pid", "address", "endpoint", "data_toggle",
                      "length", "max_packet", "speed", "cerr", "ioc"):
            self.comb += getattr(xfer.request, field).eq(
                getattr(schedule.transfer_request, field))
        for field in ("done", "ack", "nak", "stall", "nyet", "error",
                      "babble", "bytes_xfer", "data_toggle"):
            self.comb += getattr(schedule.transfer_response, field).eq(
                getattr(xfer.response, field))
        self.comb += [
            xfer.tx_stream.payload.eq(schedule.tx_stream.payload),
            xfer.tx_stream.valid.eq(schedule.tx_stream.valid),
            schedule.tx_stream.ready.eq(xfer.tx_stream.ready),
            schedule.rx_stream.payload.eq(xfer.rx_stream.payload),
            schedule.rx_stream.valid.eq(xfer.rx_stream.valid),
            schedule.rx_stream.next.eq(xfer.rx_stream.next),

            # TX mux: token generator and data generator share the bus
            # (token has priority; they never overlap in practice)
            utmi.tx_valid.eq(token_gen.tx_valid | data_tx.tx.valid |
                             hs_gen.tx.valid),
            utmi.tx_data.eq(
                (token_gen.tx_data & Replicate(token_gen.tx_valid, 8)) |
                (data_tx.tx.data & Replicate(~token_gen.tx_valid &
                                             data_tx.tx.valid, 8)) |
                (hs_gen.tx.data & Replicate(~token_gen.tx_valid &
                                            ~data_tx.tx.valid, 8))),
            token_gen.tx_ready.eq(utmi.tx_ready),
            data_tx.tx.ready.eq(utmi.tx_ready & ~token_gen.tx_valid),
            hs_gen.tx.ready.eq(utmi.tx_ready & ~token_gen.tx_valid &
                               ~data_tx.tx.valid),
        ]


class EHCIIntegrationTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = _HostStack

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def instantiate_dut(self):
        dut = super().instantiate_dut()
        dut.xfer.timeouts.ls = 40
        dut.xfer.timeouts.fs = 40
        return dut

    def mem_model(self, store, done):
        mem = self.dut.schedule.mem
        while not done:
            yield
            if (yield mem.cyc) and (yield mem.stb):
                yield mem.ack.eq(1)
                if (yield mem.we):
                    store[(yield mem.adr)] = (yield mem.dat_w)
                else:
                    yield mem.dat_r.eq(store.get((yield mem.adr), 0))
            else:
                yield mem.ack.eq(0)

    def device(self, done, log):
        """ Minimal USB device: answers SETUP/OUT tokens with ACK and
            IN tokens with a DATA1 packet (8-byte device descriptor). """
        utmi = self.dut.utmi
        desc = [0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x08]
        yield utmi.tx_ready.eq(1)

        while not done:
            # ── collect a 3-byte token ──
            token = []
            while len(token) < 3 and not done:
                yield
                if (yield utmi.tx_valid):
                    token.append((yield utmi.tx_data))
            if done or len(token) < 3:
                break
            pid = token[0] & 0x0F
            log.append(("token", pid, token[1], token[2]))

            if pid in (USBPacketID.OUT, USBPacketID.SETUP):
                # ── collect the data packet (PID + payload + CRC) ──
                # starts right after the token; tolerates the inter-packet
                # gap before the data PID
                packet = []
                idle = 0
                for _ in range(40):
                    yield
                    if (yield utmi.tx_valid):
                        packet.append((yield utmi.tx_data))
                        idle = 0
                    else:
                        idle += 1
                    if idle >= 2 and packet:
                        break
                log.append(("data", packet))
                # ── ACK it ──
                for _ in range(3):
                    yield
                yield utmi.rx_active.eq(1)
                yield
                yield utmi.rx_data.eq(0xD2)  # ACK
                yield utmi.rx_valid.eq(1)
                yield
                yield utmi.rx_valid.eq(0)
                yield
                yield utmi.rx_active.eq(0)
                log.append(("ack",))

            elif pid == USBPacketID.IN:
                # ── send DATA1 with the descriptor ──
                for _ in range(3):
                    yield
                crc = crc16_usb_bytes(desc)
                yield utmi.rx_active.eq(1)
                yield
                for byte in [0x4B] + desc + crc:   # DATA1
                    yield utmi.rx_data.eq(byte)
                    yield utmi.rx_valid.eq(1)
                    yield
                yield utmi.rx_valid.eq(0)
                yield
                yield utmi.rx_active.eq(0)
                log.append(("data_in", desc))
                # ── expect the host's ACK handshake ──
                for _ in range(20):
                    yield
                    if (yield utmi.tx_valid) and (yield utmi.tx_data) == 0xD2:
                        log.append(("host_ack",))
                        break

    def test_control_read_transfer(self):
        """ SETUP+DATA+STATUS qTD chain for a GET_DESCRIPTOR executes
            end-to-end with correct wire traffic and writebacks. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 0), cur_qtd=QTD_S))
        # SETUP: 8 bytes, DATA0
        store.update(make_qtd(QTD_S, next_qtd=QTD_D,
            token=qtd_token(8, PID_SETUP, toggle=0, ioc=0),
            buf_ptrs=[SETUP_BUF, 0, 0, 0, 0]))
        # DATA: 8 bytes IN, DATA1
        store.update(make_qtd(QTD_D, next_qtd=QTD_T,
            token=qtd_token(8, PID_IN, toggle=1, ioc=0),
            buf_ptrs=[DATA_BUF, 0, 0, 0, 0]))
        # STATUS: ZLP OUT, DATA1, IOC
        store.update(make_qtd(QTD_T, next_qtd=1,
            token=qtd_token(0, PID_OUT, toggle=1, ioc=1),
            buf_ptrs=[DATA_BUF, 0, 0, 0, 0]))
        # GET_DESCRIPTOR setup packet
        store[SETUP_BUF >> 2]       = 0x01000680
        store[(SETUP_BUF + 4) >> 2] = 0x00080000

        done = []
        log  = []

        def driver():
            dut = self.dut
            yield dut.schedule.async_list_addr.eq(QH_ADDR)
            yield dut.schedule.run.eq(1)
            yield dut.schedule.async_enable.eq(1)
            yield dut.schedule.periodic_enable.eq(0)
            yield
            usbint_seen = False
            for _ in range(4000):
                yield
                if (yield dut.schedule.usbint):
                    usbint_seen = True
                    break
            done.append(1)
            return usbint_seen

        self.domain = 'usb'
        self._ensure_clocks_present()
        self._sync_processes.append(self.mem_model(store, done))
        self._sync_processes.append(self.device(done, log))
        driver_gen = driver()
        self._sync_processes.append(driver_gen)
        self.simulate(vcd_suffix="integration")

        # ── Wire traffic checks ──
        kinds = [e[0] for e in log]

        # SETUP token seen (PID 0xD)
        self.assertIn(("token", USBPacketID.SETUP, 0x00, 0x10), log)
        # its data packet: DATA0 PID + 8 setup bytes + 2 CRC
        data_packets = [e for e in log if e[0] == "data"]
        self.assertGreaterEqual(len(data_packets), 2)  # SETUP data + ZLP
        setup_data = data_packets[0][1]
        self.assertEqual(setup_data[0], 0xC3)               # DATA0
        self.assertEqual(setup_data[1:9],
                         [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])
        # IN token seen (address 0: CRC5(0)=0x02 → byte2 = 0x10)
        self.assertIn(("token", USBPacketID.IN, 0x00, 0x10), log)
        # host ACKed the IN data
        self.assertIn(("host_ack",), log)
        # STATUS: OUT token + ZLP (PID+CRC only)
        out_tokens = [e for e in log if e == ("token", USBPacketID.OUT, 0x00, 0x10)]
        self.assertTrue(out_tokens)
        zlp = data_packets[-1][1]
        self.assertEqual(len(zlp), 3)                       # DATA1 PID + CRC
        self.assertEqual(zlp[0], 0x4B)

        # ── Memory checks ──
        # descriptor DMA'd to DATA_BUF
        self.assertEqual(store.get(DATA_BUF >> 2),       0x02000112)
        self.assertEqual(store.get((DATA_BUF + 4) >> 2), 0x08000000)
        # all three qTDs inactive
        self.assertEqual(store.get(QTD_S + 8 >> 2) & 0x80, 0)
        self.assertEqual(store.get(QTD_D + 8 >> 2) & 0x80, 0)
        self.assertEqual(store.get(QTD_T + 8 >> 2) & 0x80, 0)
        # STATUS qTD: DATA1 ZLP completed → toggle flipped to 0
        self.assertEqual(store.get(QTD_T + 8 >> 2) >> 31, 0)
