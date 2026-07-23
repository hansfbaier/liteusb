#!/usr/bin/env python3
"""
Generate WaveDrom JSON from real migen simulation traces for all liteusb modules.

Usage:
    GENERATE_VCDS=1 python3 tests/test_wavedrom.py    # also dump VCD files
    python3 tests/test_wavedrom.py                     # JSON only

Produces doc/wavedrom/*.json — one file per module-under-test.
Each JSON contains a WaveDrom diagram with signal traces captured from
an actual migen simulation of the module.
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from migen import *
from migen.sim import run_simulation

JSON_DIR = os.path.join(os.path.dirname(__file__), '..', 'doc', 'wavedrom')
os.makedirs(JSON_DIR, exist_ok=True)

VCD_DIR = os.path.join(os.path.dirname(__file__), '..', 'doc', 'vcd')
GEN_VCD = os.getenv('GENERATE_VCDS', '').strip().lower() in ('1', 'true', 'yes')


# ── WaveDrom helpers ────────────────────────────────────────────

def bools_to_wave(vals):
    wave = []
    prev = None
    for v in vals:
        b = bool(v)
        wave.append('.' if prev == b else ('1' if b else '0'))
        prev = b
    return ''.join(wave)


def ints_to_wave(vals, fmt="hex"):
    if not vals:
        return "x", []
    def fmt_v(v):
        if fmt == "hex": return hex(v)
        if fmt == "bin": return bin(v)
        return str(v)
    wave = []
    data_map = {}
    prev_sym = None
    for v in vals:
        if v not in data_map:
            idx = len(data_map) + 2
            data_map[v] = str(idx) if idx < 10 else chr(ord('a') + idx - 10)
        sym = data_map[v]
        wave.append('.' if sym == prev_sym else sym)
        prev_sym = sym
    ordered = [""] * len(data_map)
    for val, sym in data_map.items():
        idx = int(sym) - 2 if sym.isdigit() else ord(sym) - ord('a') + 8
        ordered[idx] = fmt_v(val)
    return ''.join(wave), ordered


def int_signal(name, vals, fmt="hex"):
    w, d = ints_to_wave(vals, fmt)
    sig = {"name": name, "wave": w}
    if d:
        sig["data"] = d
    return sig


def bool_signal(name, vals):
    return {"name": name, "wave": bools_to_wave(vals)}


def clk_signal(n_cycles):
    return {"name": "clk", "wave": "p" + "." * (n_cycles - 1)}


# ── Simulation harness ───────────────────────────────────────────

def sim_module(dut_module, signals, drive_fn, n_cycles,
               vcd_name=None, period_ns=10):
    """Run migen sim of *dut_module*, capturing *signals*, return traces."""
    traces = {name: [] for name in signals}

    def tb():
        for cyc in range(n_cycles):
            for s, v in drive_fn(cyc):
                yield s.eq(v)
            yield
            for name, sig in signals.items():
                traces[name].append(int((yield sig)))
        yield
        for name, sig in signals.items():
            traces[name].append(int((yield sig)))

    vcd_file = None
    if GEN_VCD and vcd_name:
        os.makedirs(VCD_DIR, exist_ok=True)
        vcd_file = os.path.join(VCD_DIR, f"{vcd_name}.vcd")

    # Modules internally use ClockDomainsRenamer("usb") — provide usb clock.
    dut_module.clock_domains += ClockDomain('usb')
    clocks = {"sys": period_ns / 1e9, "usb": period_ns / 1e9}

    run_simulation(dut_module, [tb()], clocks=clocks, vcd_name=vcd_file)
    return traces


# ═══════════════════════════════════════════════════════════════════
# Module test functions
# ═══════════════════════════════════════════════════════════════════

def test_token_detector():
    """USBTokenDetector — OUT token to address 0x3a, endpoint 0x0a."""
    from liteusb.gateware.usb.usb2.packet import USBTokenDetector
    from liteusb.gateware.interface.utmi import UTMIInterface

    utmi = UTMIInterface()
    dut = USBTokenDetector(utmi=utmi)

    sigs = {
        "rx_active": utmi.rx_active,
        "rx_valid":  utmi.rx_valid,
        "rx_data":   utmi.rx_data,
        "new_token": dut.interface.new_token,
        "pid":       dut.interface.pid,
        "address":   dut.interface.address,
        "endpoint":  dut.interface.endpoint,
    }

    def drive(cyc):
        s = [(utmi.rx_active, 0), (utmi.rx_valid, 0), (utmi.rx_data, 0)]
        # SYNC → PID=OUT(0x87) → ADDR[0:6] | ENDP[0:3] → CRC5
        if cyc == 1:   s = [(utmi.rx_active, 1), (utmi.rx_valid, 0), (utmi.rx_data, 0x80)]
        elif cyc == 2: s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, 0x87)]
        elif cyc == 3: s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, 0x50)]  # addr+endp pt1
        elif cyc == 4: s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, 0x05)]  # addr+endp pt2
        elif cyc == 5: s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, 0x02)]  # CRC5
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=12,
                        vcd_name="token_detector", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["rx_active"])),
            bool_signal("rx_active", traces["rx_active"]),
            bool_signal("rx_valid", traces["rx_valid"]),
            int_signal("rx_data", traces["rx_data"]),
            bool_signal("new_token", traces["new_token"]),
            int_signal("pid", traces["pid"]),
            int_signal("address", traces["address"]),
            int_signal("endpoint", traces["endpoint"]),
        ],
        "head": {"text": "USBTokenDetector \u2014 OUT token to address 0x3a"},
    }


def test_handshake_detector():
    """USBHandshakeDetector — ACK detection (PID 0xD2)."""
    from liteusb.gateware.usb.usb2.packet import USBHandshakeDetector
    from liteusb.gateware.interface.utmi import UTMIInterface

    utmi = UTMIInterface()
    dut = USBHandshakeDetector(utmi=utmi)

    sigs = {
        "rx_active": utmi.rx_active,
        "rx_valid":  utmi.rx_valid,
        "rx_data":   utmi.rx_data,
        "ack":       dut.detected.ack,
        "nak":       dut.detected.nak,
        "stall":     dut.detected.stall,
    }

    def drive(cyc):
        s = [(utmi.rx_active, 0), (utmi.rx_valid, 0), (utmi.rx_data, 0)]
        if cyc == 1:   s = [(utmi.rx_active, 1), (utmi.rx_valid, 0), (utmi.rx_data, 0x80)]  # SYNC
        elif cyc == 2: s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, 0xD2)]  # ACK PID
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=8,
                        vcd_name="handshake_detector", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["rx_active"])),
            bool_signal("rx_active", traces["rx_active"]),
            bool_signal("rx_valid", traces["rx_valid"]),
            int_signal("rx_data", traces["rx_data"]),
            bool_signal("ack", traces["ack"]),
            bool_signal("nak", traces["nak"]),
            bool_signal("stall", traces["stall"]),
        ],
        "head": {"text": "USBHandshakeDetector \u2014 ACK detection"},
    }


def test_handshake_generator():
    """USBHandshakeGenerator — issue_ack strobe generates ACK packet."""
    from liteusb.gateware.usb.usb2.packet import USBHandshakeGenerator

    dut = USBHandshakeGenerator()

    sigs = {
        "issue_ack": dut.issue_ack,
        "tx_valid":  dut.tx.valid,
        "tx_data":   dut.tx.data,
        "tx_ready":  dut.tx.ready,
    }

    def drive(cyc):
        s = [(dut.tx.ready, 0)]
        if cyc == 1:
            s.append((dut.issue_ack, 1))
        if cyc == 3:
            s = [(dut.tx.ready, 1)]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=7,
                        vcd_name="handshake_generator", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["issue_ack"])),
            bool_signal("issue_ack", traces["issue_ack"]),
            bool_signal("tx_valid", traces["tx_valid"]),
            int_signal("tx_data", traces["tx_data"]),
            bool_signal("tx_ready", traces["tx_ready"]),
        ],
        "head": {"text": "USBHandshakeGenerator \u2014 issue_ack strobe"},
    }


def test_data_packet_receiver():
    """USBDataPacketReceiver — DATA0 + 8 bytes + CRC16."""
    from liteusb.gateware.usb.usb2.packet import USBDataPacketReceiver, USBSpeed
    from liteusb.gateware.interface.utmi import UTMIInterface

    utmi = UTMIInterface()
    dut = USBDataPacketReceiver(utmi=utmi, standalone=True, speed=USBSpeed.FULL)

    sigs = {
        "rx_active":       utmi.rx_active,
        "rx_valid":        utmi.rx_valid,
        "rx_data":         utmi.rx_data,
        "stream_valid":    dut.stream.valid,
        "stream_next":     dut.stream.next,
        "stream_payload":  dut.stream.payload,
        "packet_complete": dut.packet_complete,
        "active_pid":      dut.active_pid,
    }

    DATA0_PID = 0xC3  # DATA0 with check bits
    payload = [0xAA, 0xBB, 0xCC, 0xDD, 0x11, 0x22, 0x33, 0x44]

    def drive(cyc):
        s = [(utmi.rx_active, 0), (utmi.rx_valid, 0), (utmi.rx_data, 0)]
        if cyc == 1:
            s = [(utmi.rx_active, 1), (utmi.rx_valid, 0), (utmi.rx_data, 0)]
        elif cyc == 2:
            s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, DATA0_PID)]
        elif 3 <= cyc <= 10:
            s = [(utmi.rx_active, 1), (utmi.rx_valid, 1),
                 (utmi.rx_data, payload[cyc - 3])]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=20,
                        vcd_name="data_packet_receiver", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["rx_active"])),
            bool_signal("rx_active", traces["rx_active"]),
            bool_signal("rx_valid", traces["rx_valid"]),
            int_signal("rx_data", traces["rx_data"]),
            bool_signal("stream.valid", traces["stream_valid"]),
            bool_signal("stream.next", traces["stream_next"]),
            int_signal("stream.payload", traces["stream_payload"]),
            bool_signal("packet_complete", traces["packet_complete"]),
            int_signal("active_pid", traces["active_pid"]),
        ],
        "head": {"text": "USBDataPacketReceiver \u2014 DATA0 + 8 bytes + CRC16"},
    }


def test_data_packet_generator():
    """USBDataPacketGenerator — 8-byte stream to TX packet."""
    from liteusb.gateware.usb.usb2.packet import USBDataPacketGenerator

    dut = USBDataPacketGenerator(standalone=True)

    sigs = {
        "stream_first":   dut.stream.first,
        "stream_last":    dut.stream.last,
        "stream_valid":   dut.stream.valid,
        "stream_payload": dut.stream.payload,
        "stream_ready":   dut.stream.ready,
        "tx_valid":       dut.tx.valid,
        "tx_data":        dut.tx.data,
    }

    payload = [0xAA, 0xBB, 0xCC, 0xDD, 0x11, 0x22, 0x33, 0x44]

    def drive(cyc):
        s = [(dut.tx.ready, 0), (dut.stream.valid, 0), (dut.stream.first, 0),
             (dut.stream.last, 0), (dut.stream.payload, 0)]
        if cyc == 0:
            s = [(dut.tx.ready, 0), (dut.stream.first, 1), (dut.stream.valid, 1),
                 (dut.stream.payload, payload[0])]
        elif 1 <= cyc <= 6:
            s = [(dut.tx.ready, 0), (dut.stream.first, 0), (dut.stream.valid, 1),
                 (dut.stream.payload, payload[cyc])]
        elif cyc == 7:
            s = [(dut.tx.ready, 0), (dut.stream.last, 1), (dut.stream.valid, 1),
                 (dut.stream.payload, payload[7])]
        if 3 <= cyc <= 14:
            s = [(dut.tx.ready, 1), (dut.stream.first, 0), (dut.stream.last, 0),
                 (dut.stream.valid, 0), (dut.stream.payload, 0)]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=20,
                        vcd_name="data_packet_generator", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["stream_valid"])),
            bool_signal("stream.first", traces["stream_first"]),
            bool_signal("stream.last", traces["stream_last"]),
            bool_signal("stream.valid", traces["stream_valid"]),
            int_signal("stream.payload", traces["stream_payload"]),
            bool_signal("tx.valid", traces["tx_valid"]),
            int_signal("tx.data", traces["tx_data"]),
            bool_signal("stream.ready", traces["stream_ready"]),
        ],
        "head": {"text": "USBDataPacketGenerator \u2014 8-byte stream to TX packet"},
    }


def test_reset_sequencer():
    """USBResetSequencer — Full Speed Reset to HS Detection."""
    from liteusb.gateware.usb.usb2.reset import USBResetSequencer

    dut = USBResetSequencer()

    sigs = {
        "line_state":     dut.line_state,
        "vbus_connected": dut.vbus_connected,
        "bus_reset":      dut.bus_reset,
        "current_speed":  dut.current_speed,
        "operating_mode": dut.operating_mode,
    }

    def drive(cyc):
        s = [(dut.vbus_connected, 1), (dut.disconnect, 0),
             (dut.line_state, 1),    # J state (non-SE0)
             (dut.low_speed_only, 0), (dut.full_speed_only, 0)]
        if 4 <= cyc < 30:      # SE0 for reset duration
            s += [(dut.line_state, 0)]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=80,
                        vcd_name="reset_sequencer", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["line_state"])),
            int_signal("line_state", traces["line_state"]),
            bool_signal("vbus_connected", traces["vbus_connected"]),
            bool_signal("bus_reset", traces["bus_reset"]),
            int_signal("current_speed", traces["current_speed"]),
            int_signal("operating_mode", traces["operating_mode"]),
        ],
        "head": {"text": "USBResetSequencer \u2014 Full Speed Reset to HS Detection"},
    }


def test_control_endpoint():
    """USBControlEndpoint — EP0 stages: SETUP → DATA_IN → STATUS_OUT."""
    from liteusb.gateware.usb.usb2.control import USBControlEndpoint
    from liteusb.gateware.interface.utmi import UTMIInterface

    utmi = UTMIInterface()
    dut = USBControlEndpoint(utmi=utmi, endpoint_number=0)

    sigs = {
        "rx_valid":       utmi.rx_valid,
        "rx_data":        utmi.rx_data,
        "rx_active":      utmi.rx_active,
        "tx_valid":       utmi.tx_valid,
        "tx_data":        utmi.tx_data,
    }

    # Drive a simple SETUP transaction: PID + 8 data bytes
    setup_data = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00]  # GET_DESCRIPTOR(DEVICE)

    def drive(cyc):
        s = [(utmi.rx_active, 0), (utmi.rx_valid, 0), (utmi.rx_data, 0),
             (utmi.tx_ready, 0)]
        if cyc == 1:   s = [(utmi.rx_active, 1), (utmi.rx_valid, 0), (utmi.rx_data, 0), (utmi.tx_ready, 0)]
        elif cyc == 2: s = [(utmi.rx_active, 1), (utmi.rx_valid, 1), (utmi.rx_data, 0x2D), (utmi.tx_ready, 0)]  # SETUP PID
        elif 3 <= cyc <= 10:
            s = [(utmi.rx_active, 1), (utmi.rx_valid, 1),
                 (utmi.rx_data, setup_data[cyc - 3]), (utmi.tx_ready, 0)]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=20,
                        vcd_name="control_endpoint", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["rx_valid"])),
            bool_signal("rx.active", traces["rx_active"]),
            bool_signal("rx.valid", traces["rx_valid"]),
            int_signal("rx.data", traces["rx_data"]),
            bool_signal("tx.valid", traces["tx_valid"]),
            int_signal("tx.data", traces["tx_data"]),
        ],
        "head": {"text": "USBControlEndpoint \u2014 EP0 stages: SETUP, DATA_IN, STATUS_OUT"},
    }


def test_transfer_in():
    """USBInTransferManager — Double-buffered IN with PID toggle."""
    from liteusb.gateware.usb.usb2.transfer import USBInTransferManager

    dut = USBInTransferManager(max_packet_size=8)

    sigs = {
        "transfer_valid": dut.transfer_stream.valid,
        "transfer_last":  dut.transfer_stream.last,
        "transfer_first": dut.transfer_stream.first,
        "transfer_payload": dut.transfer_stream.payload,
        "transfer_ready": dut.transfer_stream.ready,
        "data_pid":       dut.data_pid,
        "packet_valid":   dut.packet_stream.valid,
        "packet_payload": dut.packet_stream.payload,
    }

    payload = [0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49]

    def drive(cyc):
        s = [(dut.transfer_stream.valid, 0), (dut.transfer_stream.first, 0),
             (dut.transfer_stream.last, 0), (dut.transfer_stream.payload, 0),
             (dut.transfer_stream.ready, 0),
             (dut.active, 1)]
        if cyc == 0:
            s += [(dut.transfer_stream.valid, 1), (dut.transfer_stream.first, 1),
                  (dut.transfer_stream.payload, payload[0])]
        elif 1 <= cyc <= 6:
            s += [(dut.transfer_stream.valid, 1), (dut.transfer_stream.first, 0),
                  (dut.transfer_stream.payload, payload[cyc])]
        elif cyc == 7:
            s += [(dut.transfer_stream.valid, 1), (dut.transfer_stream.last, 1),
                  (dut.transfer_stream.payload, payload[7])]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=15,
                        vcd_name="transfer_in", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["transfer_valid"])),
            bool_signal("transfer.valid", traces["transfer_valid"]),
            bool_signal("transfer.last", traces["transfer_last"]),
            int_signal("data_pid", traces["data_pid"]),
            bool_signal("packet.valid", traces["packet_valid"]),
            int_signal("packet.payload", traces["packet_payload"]),
        ],
        "head": {"text": "USBInTransferManager \u2014 Double-buffered IN with PID toggle"},
    }


def test_descriptor_generator():
    """USBDescriptorStreamGenerator — ROM data to USB stream."""
    from liteusb.gateware.usb.usb2.descriptor import USBDescriptorStreamGenerator

    descriptor_data = [0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
                       0x1B, 0x1B, 0x20, 0x50, 0x00, 0x00, 0x01, 0x02,
                       0x00, 0x01]

    dut = USBDescriptorStreamGenerator(data=descriptor_data, domain="sys")

    sigs = {
        "start":       dut.start,
        "tx_valid":    dut.stream.valid,
        "tx_first":    dut.stream.first,
        "tx_last":     dut.stream.last,
        "tx_payload":  dut.stream.payload,
        "tx_ready":    dut.stream.ready,
    }

    def drive(cyc):
        s = [(dut.stream.ready, 0), (dut.start, 0)]
        if cyc == 1:
            s = [(dut.stream.ready, 0), (dut.start, 1)]
        if cyc >= 3:
            s = [(dut.stream.ready, 1), (dut.start, 0)]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=30,
                        vcd_name="descriptor_generator", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["start"])),
            bool_signal("start", traces["start"]),
            bool_signal("tx.valid", traces["tx_valid"]),
            bool_signal("tx.first", traces["tx_first"]),
            bool_signal("tx.last", traces["tx_last"]),
            int_signal("tx.payload", traces["tx_payload"]),
            bool_signal("tx.ready", traces["tx_ready"]),
        ],
        "head": {"text": "USBDescriptorStreamGenerator \u2014 ROM data to USB stream"},
    }


def test_stream_boundary():
    """USBOutStreamBoundaryDetector — first/last byte detection."""
    from liteusb.gateware.usb.stream import USBOutStreamBoundaryDetector

    dut = USBOutStreamBoundaryDetector(domain="sys")

    in_stream = dut.unprocessed_stream
    out_stream = dut.processed_stream

    sigs = {
        "in_valid":    in_stream.valid,
        "in_payload":  in_stream.payload,
        "in_next":     in_stream.next,
        "out_valid":   out_stream.valid,
        "out_payload": out_stream.payload,
        "first":       dut.first,
        "last":        dut.last,
    }

    payload = [0xAA, 0xBB, 0xCC, 0xDD]

    def drive(cyc):
        s = [(in_stream.next, 0), (in_stream.valid, 0),
             (in_stream.payload, 0)]
        if 1 <= cyc <= 4:
            s = [(in_stream.next, 1), (in_stream.valid, 1),
                 (in_stream.payload, payload[cyc - 1])]
        return s

    traces = sim_module(dut, sigs, drive, n_cycles=12,
                        vcd_name="stream_boundary", period_ns=16)

    return {
        "signal": [
            clk_signal(len(traces["in_valid"])),
            bool_signal("unproc.valid", traces["in_valid"]),
            int_signal("unproc.payload", traces["in_payload"]),
            bool_signal("proc.valid", traces["out_valid"]),
            int_signal("proc.payload", traces["out_payload"]),
            bool_signal("first", traces["first"]),
            bool_signal("last", traces["last"]),
        ],
        "head": {"text": "USBOutStreamBoundaryDetector \u2014 first/last byte detection"},
    }


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════
ALL_TESTS = {
    "token_detect":       test_token_detector,
    "handshake_detect":   test_handshake_detector,
    "handshake_gen":      test_handshake_generator,
    "data_rx":            test_data_packet_receiver,
    "data_gen":           test_data_packet_generator,
    "reset_seq":          test_reset_sequencer,
    "control_ep0":        test_control_endpoint,
    "transfer_in":        test_transfer_in,
    "descriptor":         test_descriptor_generator,
    "stream_boundary":    test_stream_boundary,
}


def main():
    print(f"Generating WaveDrom JSON from migen simulations...")
    print(f"  VCD dumps: {'ON' if GEN_VCD else 'OFF (set GENERATE_VCDS=1 to enable)'}")
    print()

    ok = 0
    for name, fn in ALL_TESTS.items():
        print(f"  Simulating {name}...", end=" ", flush=True)
        try:
            wave = fn()
            path = os.path.join(JSON_DIR, f"{name}.json")
            with open(path, "w") as f:
                json.dump(wave, f, indent=2)
            n_sigs = len(wave.get("signal", []))
            print(f"\u2192 {name}.json ({n_sigs} signals)")
            ok += 1
        except Exception as e:
            import traceback
            print(f"\u2717 FAILED: {e}")
            traceback.print_exc()

    print(f"\n{ok}/{len(ALL_TESTS)} diagrams written to {JSON_DIR}/")


if __name__ == "__main__":
    main()
