#!/usr/bin/env python3
"""
Generate WaveDrom JSON from actual simulation traces for liteusb modules.
Uses migen simulation to capture real signal behavior.
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from migen import *
from migen.sim import run_simulation

JSON_DIR = os.path.join(os.path.dirname(__file__), '..', 'doc', 'wavedrom')
os.makedirs(JSON_DIR, exist_ok=True)

# ── helpers ───────────────────────────────────────────────────

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
    prev = None
    prev_sym = None
    for v in vals:
        if v not in data_map:
            idx = len(data_map) + 2
            data_map[v] = str(idx) if idx < 10 else chr(ord('a') + idx - 10)
        sym = data_map[v]
        wave.append('.' if sym == prev_sym else sym)
        prev_sym = sym
        prev = v
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

def run_and_trace(cycles, drive_fn, signal_map):
    """Run simulation capturing signals. drive_fn(cyc) → [(sig, val), ...]"""
    traces = {name: [] for name in signal_map}

    def tb():
        for cyc in range(cycles):
            drv = drive_fn(cyc)
            for sig, val in drv:
                yield sig.eq(val)
            yield
            for name, sig in signal_map.items():
                traces[name].append(int((yield sig)))
        yield
        for name, sig in signal_map.items():
            traces[name].append(int((yield sig)))

    # Build minimal dut
    class Wrapper(Module):
        pass
    dut = Wrapper()
    for sig in signal_map.values():
        if isinstance(sig, Signal):
            pass  # already attached

    run_simulation(dut, [tb()], clocks={"sys": 10})
    return traces


# ═══════════════════════════════════════════════════════════════
# 1. USBTokenDetector
# ═══════════════════════════════════════════════════════════════
def test_token_detector():
    from liteusb.gateware.usb.usb2.packet import USBTokenDetector
    from liteusb.gateware.interface.utmi import UTMIInterface, UTMITransmitInterface, UTMIReceiveInterface

    utmi_rx = UTMIReceiveInterface()
    utmi_tx = UTMITransmitInterface()
    detector = USBTokenDetector(utmi=utmi_rx, utmi_tx=utmi_tx)
    detector.address = Signal(7, reset=0x3a)

    sigs = {
        "rx_active": utmi_rx.rx_active,
        "rx_valid": utmi_rx.rx_valid,
        "rx_data": utmi_rx.rx_data,
        "new_token": detector.interface.new_token,
        "pid": detector.interface.pid,
        "address_d": detector.interface.address,
        "endpoint": detector.interface.endpoint,
    }

    packets = [0x80, 0x87, 0, 0, 0]  # SYNC placeholder, PID=OUT, addr, endp, CRC5 placeholder
    def drive(cyc):
        stmts = [(utmi_rx.rx_active, 0), (utmi_rx.rx_valid, 0), (utmi_rx.rx_data, 0)]
        if 1 <= cyc <= 5:
            stmts = [(utmi_rx.rx_active, 1), (utmi_rx.rx_valid, 1), (utmi_rx.rx_data, packets[cyc-1] if cyc-1 < len(packets) else 0)]
        return stmts

    class Wrapper(Module):
        pass
    dut = Wrapper()
    dut.submodules.det = detector
    traces = {}
    for name, sig in sigs.items():
        traces[name] = []

    def tb():
        for cyc in range(10):
            drv = drive(cyc)
            for s, v in drv:
                yield s.eq(v)
            yield
            for name, sig in sigs.items():
                traces[name].append(int((yield sig)))
        yield
        for name, sig in sigs.items():
            traces[name].append(int((yield sig)))

    run_simulation(dut, [tb()], clocks={"sys": 10})

    return {
        "signal": [
            {"name": "clk", "wave": "p" + "." * (len(traces["rx_active"]) - 1)},
            bool_signal("rx_active", traces["rx_active"]),
            bool_signal("rx_valid", traces["rx_valid"]),
            int_signal("rx_data", traces["rx_data"]),
            bool_signal("new_token", traces["new_token"]),
            int_signal("pid", traces["pid"]),
            int_signal("address", traces["address_d"]),
            int_signal("endpoint", traces["endpoint"]),
        ],
        "head": {"text": "USBTokenDetector — OUT token to 0x3a, endpoint 0"},
    }


# ═══════════════════════════════════════════════════════════════
# 2. USBHandshakeDetector
# ═══════════════════════════════════════════════════════════════
def test_handshake_detector():
    from liteusb.gateware.usb.usb2.packet import USBHandshakeDetector
    from liteusb.gateware.interface.utmi import UTMIReceiveInterface

    utmi_rx = UTMIReceiveInterface()
    detector = USBHandshakeDetector(utmi=utmi_rx)

    sigs = {
        "rx_active": utmi_rx.rx_active,
        "rx_valid": utmi_rx.rx_valid,
        "rx_data": utmi_rx.rx_data,
        "ack": detector.detected.ack,
        "nak": detector.detected.nak,
        "stall": detector.detected.stall,
    }

    ack_pid = 0xD2
    def drive(cyc):
        stmts = [(utmi_rx.rx_active, 0), (utmi_rx.rx_valid, 0), (utmi_rx.rx_data, 0)]
        if 1 <= cyc <= 3:
            stmts = [(utmi_rx.rx_active, 1), (utmi_rx.rx_valid, 1), (utmi_rx.rx_data, ack_pid if cyc == 2 else 0)]
        return stmts

    class Wrapper(Module):
        pass
    dut = Wrapper()
    dut.submodules.det = detector
    traces = {name: [] for name in sigs}

    def tb():
        for cyc in range(8):
            drv = drive(cyc)
            for s, v in drv:
                yield s.eq(v)
            yield
            for name, sig in sigs.items():
                traces[name].append(int((yield sig)))
        yield
        for name, sig in sigs.items():
            traces[name].append(int((yield sig)))

    run_simulation(dut, [tb()], clocks={"sys": 10})

    return {
        "signal": [
            {"name": "clk", "wave": "p" + "." * (len(traces["rx_active"]) - 1)},
            bool_signal("rx_active", traces["rx_active"]),
            bool_signal("rx_valid", traces["rx_valid"]),
            int_signal("rx_data", traces["rx_data"]),
            bool_signal("ack", traces["ack"]),
            bool_signal("nak", traces["nak"]),
            bool_signal("stall", traces["stall"]),
        ],
        "head": {"text": "USBHandshakeDetector — ACK (PID 0xD2) detection"},
    }


# ═══════════════════════════════════════════════════════════════
# 3. USBHandshakeGenerator
# ═══════════════════════════════════════════════════════════════
def test_handshake_generator():
    from liteusb.gateware.usb.usb2.packet import USBHandshakeGenerator
    from liteusb.gateware.interface.utmi import UTMITransmitInterface

    utmi_tx = UTMITransmitInterface()
    gen = USBHandshakeGenerator(utmi=utmi_tx)

    sigs = {
        "issue_ack": gen.issue_ack,
        "tx_valid": utmi_tx.tx_valid,
        "tx_data": utmi_tx.tx_data,
        "tx_ready": utmi_tx.tx_ready,
    }

    def drive(cyc):
        stmts = [(utmi_tx.tx_ready, 0)]
        if cyc == 1:
            stmts.append((gen.issue_ack, 1))
        if cyc == 3:
            stmts = [(utmi_tx.tx_ready, 1)]
        return stmts

    class Wrapper(Module):
        pass
    dut = Wrapper()
    dut.submodules.gen = gen
    traces = {name: [] for name in sigs}

    def tb():
        for cyc in range(7):
            drv = drive(cyc)
            for s, v in drv:
                yield s.eq(v)
            yield
            for name, sig in sigs.items():
                traces[name].append(int((yield sig)))
        yield
        for name, sig in sigs.items():
            traces[name].append(int((yield sig)))

    run_simulation(dut, [tb()], clocks={"sys": 10})

    return {
        "signal": [
            {"name": "clk", "wave": "p" + "." * (len(traces["issue_ack"]) - 1)},
            bool_signal("issue_ack", traces["issue_ack"]),
            bool_signal("tx_valid", traces["tx_valid"]),
            int_signal("tx_data", traces["tx_data"]),
            bool_signal("tx_ready", traces["tx_ready"]),
        ],
        "head": {"text": "USBHandshakeGenerator — issue_ack strobe to ACK packet"},
    }


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════
ALL_TESTS = {
    "token_detect":        test_token_detector,
    "handshake_detect":    test_handshake_detector,
    "handshake_gen":       test_handshake_generator,
}

if __name__ == "__main__":
    for name, fn in ALL_TESTS.items():
        print(f"Testing {name}...")
        wave = fn()
        path = os.path.join(JSON_DIR, f"{name}.json")
        with open(path, "w") as f:
            json.dump(wave, f, indent=2)
        print(f"  → {path}")
    print(f"\n{len(ALL_TESTS)} diagrams generated")
