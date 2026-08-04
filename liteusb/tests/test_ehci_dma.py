#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
#

""" EHCI schedule DMA tests — QH/qTD traversal against a simulated memory.

Drives the schedule engine with a dict-backed Wishbone memory model and
a mocked transfer engine; verifies EHCI §3/§4 behavior:
  - Async schedule: QH fetch, qTD fetch, payload DMA, token writeback
  - Transaction chunking by MaxPacketSize with data-toggle sequencing
  - IOC -> USBINT, error -> USBERRINT, IAA doorbell
  - NAK: no writeback, qTD stays Active
  - STALL/error: Halted (+XactErr) writeback
  - Periodic schedule: frame-list entry -> interrupt QH
  - Buffer page crossing
  - qTD chaining

Simulation structure: each test registers all processes (memory model,
transfer mock, driver) BEFORE starting the simulation — migen snapshots
the process list when the simulator starts.
"""

from liteusb.tests.test_case import LiteUSBUSBTestCase
from liteusb.gateware.usb.usb2                 import USBPacketID, USBSpeed
from liteusb.gateware.usb.usb2.host.schedule   import EHCIScheduleProcessor


# ── Memory layout helpers ───────────────────────────────────────────────────

def make_qh(base, hlp, ep_char, cur_qtd):
    """ QH dwords at byte address `base`. """
    return {
        (base + 0)  >> 2: hlp,
        (base + 4)  >> 2: ep_char,
        (base + 8)  >> 2: 0,          # ep_cap
        (base + 12) >> 2: cur_qtd,
    }


def make_qtd(base, next_qtd, token, buf_ptrs):
    """ qTD dwords at byte address `base`. """
    d = {
        (base + 0) >> 2: next_qtd,
        (base + 4) >> 2: 1,           # alt = terminate
        (base + 8) >> 2: token,
    }
    for i, p in enumerate(buf_ptrs):
        d[(base + 12 + 4 * i) >> 2] = p
    return d


def qtd_token(total, pid, toggle=0, ioc=0, cerr=3, cpage=0, status=0x80):
    """ Build a qTD token dword (EHCI §3.5.3). """
    return ((toggle & 1) << 31 | (total & 0x7FFF) << 16 | (ioc & 1) << 15 |
            (cpage & 7) << 12 | (cerr & 3) << 10 | (pid & 3) << 8 |
            (status & 0xFF))


def ep_char(max_packet, eps, endpoint, address):
    """ Build a QH endpoint-characteristics dword (EHCI §3.6.2). """
    return ((max_packet & 0x7FF) << 16 | (eps & 3) << 12 |
            (endpoint & 0xF) << 8 | (address & 0x7F))


QH_ADDR   = 0x1000
QTD_ADDR  = 0x1100
BUF_ADDR  = 0x2000

EPS_FULL  = 0
EPS_LOW   = 1
EPS_HIGH  = 2

PID_OUT   = 0
PID_IN    = 1
PID_SETUP = 2


# ── Test bench scaffolding ──────────────────────────────────────────────────

class EHCIScheduleDmaTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = EHCIScheduleProcessor
    FRAGMENT_ARGUMENTS  = {"num_ports": 1}

    SYNC_CLOCK_FREQUENCY = None
    USB_CLOCK_FREQUENCY  = 60e6

    def run_sim(self, *processes, vcd_suffix=None):
        """ Start the simulation with all processes registered upfront. """
        self.domain = 'usb'
        self._ensure_clocks_present()
        for p in processes:
            self._sync_processes.append(p)
        self.simulate(vcd_suffix=vcd_suffix)

    def mem_model(self, store, done):
        """ Wishbone slave model: ack next cycle, dict-backed storage. """
        mem = self.dut.mem
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

    def mock_transfer(self, done, transfers):
        """ Mock the transfer engine: execute the given transfer list.

        transfers: list of dicts:
            rx:    bytes to supply for IN transfers (default none)
            result: "ack" (default) / "nak" / "stall" / "error"
            capture: list to append request fields to
        """
        dut = self.dut
        for t in transfers:
            while not (yield dut.transfer_request.valid):
                if done:
                    return
                yield
            if t.get("capture") is not None:
                t["capture"].append({
                    "pid":        (yield dut.transfer_request.pid),
                    "address":    (yield dut.transfer_request.address),
                    "endpoint":   (yield dut.transfer_request.endpoint),
                    "length":     (yield dut.transfer_request.length),
                    "toggle":     (yield dut.transfer_request.data_toggle),
                    "max_packet": (yield dut.transfer_request.max_packet),
                    "speed":      (yield dut.transfer_request.speed),
                })
            # consume OUT payload bytes while they stream
            sent = 0
            yield dut.tx_stream.ready.eq(1)
            for _ in range(t.get("ack_after", 16)):
                yield
                if (yield dut.tx_stream.valid) and (yield dut.tx_stream.ready):
                    sent += 1
            yield dut.tx_stream.ready.eq(0)
            # supply IN payload bytes
            rx = t.get("rx", [])
            for byte in rx:
                yield dut.rx_stream.payload.eq(byte)
                yield dut.rx_stream.valid.eq(1)
                yield dut.rx_stream.next.eq(1)
                yield
            yield dut.rx_stream.valid.eq(0)
            yield dut.rx_stream.next.eq(0)
            # complete
            result = t.get("result", "ack")
            yield dut.transfer_response.done.eq(1)
            yield dut.transfer_response.ack.eq(result == "ack")
            yield dut.transfer_response.nak.eq(result == "nak")
            yield dut.transfer_response.stall.eq(result == "stall")
            yield dut.transfer_response.error.eq(result == "error")
            yield dut.transfer_response.bytes_xfer.eq(
                len(rx) if rx else sent)
            yield
            yield dut.transfer_response.done.eq(0)
            yield dut.transfer_response.ack.eq(0)
            yield dut.transfer_response.nak.eq(0)
            yield dut.transfer_response.stall.eq(0)
            yield dut.transfer_response.error.eq(0)
            yield

    def arm_async_driver(self, done, cycles=2000, watch=None):
        """ Enable the async schedule and run until `watch` fires. """
        dut = self.dut
        yield dut.async_list_addr.eq(QH_ADDR)
        yield dut.run.eq(1)
        yield dut.async_enable.eq(1)
        yield dut.periodic_enable.eq(0)
        yield
        fired = []
        for _ in range(cycles):
            yield
            if watch is not None and (yield getattr(dut, watch)):
                fired.append(1)
                break
        done.append(1)
        return fired


# ── Tests ───────────────────────────────────────────────────────────────────

    def test_async_setup_out(self):
        """ SETUP qTD (8 bytes OUT): request fields, payload DMA, token
            writeback (Active cleared), IOC interrupt. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 3), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(8, PID_SETUP, toggle=0, ioc=1),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))
        store[BUF_ADDR >> 2]       = 0x01000680
        store[(BUF_ADDR + 4) >> 2] = 0x00080000

        done = []
        requests = []
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done, [{"capture": requests}]),
            self.arm_async_driver(done, watch="usbint"),
        )

        self.assertEqual(len(requests), 1)
        req = requests[0]
        self.assertEqual(req["pid"], USBPacketID.SETUP)
        self.assertEqual(req["address"], 3)
        self.assertEqual(req["endpoint"], 0)
        self.assertEqual(req["length"], 8)
        self.assertEqual(req["max_packet"], 8)
        self.assertEqual(req["speed"], USBSpeed.FULL)
        self.assertEqual(req["toggle"], 0)

        token = store[QTD_ADDR + 8 >> 2]
        self.assertEqual(token & 0x80, 0)           # Active cleared
        self.assertEqual(token >> 31, 1)            # toggle flipped
        self.assertEqual((token >> 16) & 0x7FFF, 0) # bytes remaining = 0
        self.assertEqual((token >> 8) & 3, PID_SETUP)

    def test_async_in_with_dma_writeback(self):
        """ IN qTD: received bytes DMA'd to the buffer, token updated. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 1, 5), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(4, PID_IN, toggle=1, ioc=0),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))

        done = []
        requests = []

        def driver():
            yield dut.async_list_addr.eq(QH_ADDR)
            yield dut.run.eq(1)
            yield dut.async_enable.eq(1)
            yield
            for _ in range(2000):
                yield
                if store.get(QTD_ADDR + 8 >> 2, 0x80) & 0x80 == 0:
                    break
            done.append(1)

        dut = self.dut
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done, [{"rx": [0x00, 0x00, 0x04, 0x00],
                                       "capture": requests}]),
            driver(),
        )

        self.assertEqual(store.get(BUF_ADDR >> 2), 0x00040000)
        token = store[QTD_ADDR + 8 >> 2]
        self.assertEqual(token & 0x80, 0)
        self.assertEqual(token >> 31, 0)     # toggle 1 -> 0
        self.assertEqual((token >> 16) & 0x7FFF, 0)

    def test_chunking_by_max_packet(self):
        """ 18-byte IN with MaxPacket=8 → 3 chunks (8/8/2), toggles
            1/0/1, advancing buffer address. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 1), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(18, PID_IN, toggle=1, ioc=0),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))

        done = []
        requests = []
        chunk_data = [[0x10] * 8, [0x11] * 8, [0x30, 0x31]]

        def driver():
            yield dut.async_list_addr.eq(QH_ADDR)
            yield dut.run.eq(1)
            yield dut.async_enable.eq(1)
            yield
            for _ in range(3000):
                yield
                if store.get(QTD_ADDR + 8 >> 2, 0x80) & 0x80 == 0:
                    break
            done.append(1)

        dut = self.dut
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done,
                [{"rx": d, "capture": requests} for d in chunk_data]),
            driver(),
        )

        self.assertEqual([r["length"] for r in requests], [8, 8, 2])
        self.assertEqual([r["toggle"] for r in requests], [1, 0, 1])

        # All 18 bytes landed: chunk0 at +0/+4, chunk1 at +8/+12,
        # chunk2's 2 bytes at +16 (upper lanes stale)
        self.assertEqual(store.get(BUF_ADDR >> 2), 0x10101010)
        self.assertEqual(store.get((BUF_ADDR + 8) >> 2), 0x11111111)
        self.assertEqual(store.get((BUF_ADDR + 16) >> 2), 0x00003130)

        token = store[QTD_ADDR + 8 >> 2]
        self.assertEqual(token & 0x80, 0)
        self.assertEqual(token >> 31, 0)     # 3 flips from 1 -> 0

    def test_nak_leaves_qtd_active(self):
        """ NAK: transaction retired, NO writeback, qTD stays Active. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 1), cur_qtd=QTD_ADDR))
        original_token = qtd_token(8, PID_IN, toggle=1)
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=original_token, buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))

        done = []

        def driver():
            yield dut.async_list_addr.eq(QH_ADDR)
            yield dut.run.eq(1)
            yield dut.async_enable.eq(1)
            yield
            for _ in range(300):
                yield
            done.append(1)

        dut = self.dut
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done, [{"result": "nak"}]),
            driver(),
        )

        self.assertEqual(store.get(QTD_ADDR + 8 >> 2), original_token)

    def test_stall_writes_halted(self):
        """ STALL: token written back Halted, no XactErr, USBERRINT. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 1), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(8, PID_OUT, toggle=0),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))
        store[BUF_ADDR >> 2]     = 0xAAAAAAAA
        store[(BUF_ADDR+4) >> 2] = 0xAAAAAAAA

        done = []
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done, [{"result": "stall"}]),
            self.arm_async_driver(done, watch="usberr"),
        )

        token = store.get(QTD_ADDR + 8 >> 2, 0x80)
        self.assertEqual(token & 0x80, 0)     # Active cleared
        self.assertEqual(token & 0x40, 0x40)  # Halted
        self.assertEqual(token & 0x08, 0)     # no XactErr on STALL

    def test_error_writes_halted_xacterr(self):
        """ Retries exhausted: Halted + XactErr + USBERRINT. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 1), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(8, PID_OUT, toggle=0),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))
        store[BUF_ADDR >> 2]     = 0xAAAAAAAA
        store[(BUF_ADDR+4) >> 2] = 0xAAAAAAAA

        done = []
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done, [{"result": "error"}]),
            self.arm_async_driver(done, watch="usberr"),
        )

        token = store.get(QTD_ADDR + 8 >> 2, 0x80)
        self.assertEqual(token & 0x80, 0)
        self.assertEqual(token & 0x40, 0x40)  # Halted
        self.assertEqual(token & 0x08, 0x08)  # XactErr

    def test_iaa_doorbell(self):
        """ Async advance with USBCMD.IAA set pulses the IAA strobe. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 1), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(0, PID_OUT, toggle=1),  # ZLP
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))

        done = []

        def driver():
            yield dut.async_list_addr.eq(QH_ADDR)
            yield dut.run.eq(1)
            yield dut.async_enable.eq(1)
            yield dut.periodic_enable.eq(0)
            yield dut.interrupt_on_aa.eq(1)
            yield
            fired = False
            for _ in range(2000):
                yield
                if (yield dut.iaa):
                    fired = True
                    break
            done.append(1)
            return fired

        dut = self.dut
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done, [{}]),
            driver(),
        )

        token = store.get(QTD_ADDR + 8 >> 2, 0x80)
        self.assertEqual(token & 0x80, 0)

    def test_periodic_interrupt_qh(self):
        """ Periodic schedule: frame-list entry -> interrupt QH executed
            on the SOF microframe. """
        store = {}
        store[0x4000 >> 2] = QH_ADDR | 0x2   # type=QH
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 1, 2), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(8, PID_IN, toggle=1, ioc=1),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))

        done = []
        requests = []

        def driver():
            yield dut.frame_list_base.eq(0x4000)
            yield dut.run.eq(1)
            yield dut.periodic_enable.eq(1)
            yield dut.async_enable.eq(0)
            yield
            yield dut.sof_strobe.eq(1)
            yield
            yield dut.sof_strobe.eq(0)
            for _ in range(2000):
                yield
                if (yield dut.usbint):
                    break
            done.append(1)

        dut = self.dut
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done,
                [{"rx": [1, 2, 3, 4, 5, 6, 7, 8], "capture": requests}]),
            driver(),
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["pid"], USBPacketID.IN)
        self.assertEqual(requests[0]["address"], 2)
        self.assertEqual(requests[0]["endpoint"], 1)

    def test_page_crossing(self):
        """ Transfer spanning two 4K pages: payload DMA crosses the
            boundary, qTD completes. """
        page0 = 0x3000
        page1 = 0x4000
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 1), cur_qtd=QTD_ADDR))
        # 10 bytes OUT starting 4 bytes before the page end
        store.update(make_qtd(QTD_ADDR, next_qtd=1,
            token=qtd_token(10, PID_OUT, toggle=0),
            buf_ptrs=[page0 | 0xFFC, page1, 0, 0, 0]))
        store[(page0 + 0xFFC) >> 2] = 0x44332211
        store[(page1 + 0x000) >> 2] = 0x88776655
        store[(page1 + 0x004) >> 2] = 0x0000AA99

        done = []
        requests = []

        def driver():
            yield dut.async_list_addr.eq(QH_ADDR)
            yield dut.run.eq(1)
            yield dut.async_enable.eq(1)
            yield
            for _ in range(3000):
                yield
                if store.get(QTD_ADDR + 8 >> 2, 0x80) & 0x80 == 0:
                    break
            done.append(1)

        dut = self.dut
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done,
                [{"capture": requests}, {"capture": requests}]),
            driver(),
        )

        self.assertEqual([r["length"] for r in requests], [8, 2])
        token = store[QTD_ADDR + 8 >> 2]
        self.assertEqual(token & 0x80, 0)

    def test_qtd_chain(self):
        """ Two chained qTDs (SETUP -> STATUS) execute in order. """
        store = {}
        store.update(make_qh(QH_ADDR, hlp=1,
            ep_char=ep_char(8, EPS_FULL, 0, 3), cur_qtd=QTD_ADDR))
        store.update(make_qtd(QTD_ADDR, next_qtd=QTD_ADDR + 0x20,
            token=qtd_token(8, PID_SETUP, toggle=0, ioc=0),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))
        store.update(make_qtd(QTD_ADDR + 0x20, next_qtd=1,
            token=qtd_token(0, PID_IN, toggle=1, ioc=1),
            buf_ptrs=[BUF_ADDR, 0, 0, 0, 0]))
        store[BUF_ADDR >> 2]       = 0x01000680
        store[(BUF_ADDR + 4) >> 2] = 0x00080000

        done = []
        requests = []
        self.run_sim(
            self.mem_model(store, done),
            self.mock_transfer(done,
                [{"capture": requests}, {"capture": requests}]),
            self.arm_async_driver(done, watch="usbint"),
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["pid"], USBPacketID.SETUP)
        self.assertEqual(requests[1]["pid"], USBPacketID.IN)
        self.assertEqual(requests[1]["toggle"], 1)
        # Both tokens written back inactive
        self.assertEqual(store.get(QTD_ADDR + 8 >> 2) & 0x80, 0)
        self.assertEqual(store.get(QTD_ADDR + 0x28 >> 2) & 0x80, 0)
