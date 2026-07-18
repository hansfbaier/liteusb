# LiteUSB Porting Summary

## Overview

This document summarizes the porting of LUNA (USB FPGA gateware) from Amaranth HDL to Migen/LiteX framework.

## Statistics

- **Total Python files**: 86
- **Gateware modules**: 39
- **Test files**: 17
- **Lines of code**: ~15,600

## Test Suite Status

**44 tests discovered — 44 passing, 0 errors**

| Test Module | Tests | Status |
|-------------|-------|--------|
| `test_usb2_packet` | 18 | All passing |
| `test_usb2_endpoints` | 2 | All passing |
| `test_usb2_descriptor` | 7 | All passing |
| `test_usb2_transfer` | 4 | All passing |
| `test_usb2_request` | 3 | All passing |
| `test_usb2_reset` | 1 | Passing |
| `test_usb_stream` | 1 | Passing |
| `test_ulpi` | 8 | All passing |

The `test_usb2_device` module contains enumeration tests that require the full USB device test harness; these are not discovered by the standard unittest runner and need the custom `run_tests.py` script.

## Ported Modules

### Core USB Stack

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/device.py` | Ported | Main USB device controller |
| `gateware/usb/usb2/device.py` | Ported | USB2 device implementation |
| `gateware/usb/usb2/control.py` | Ported | Control endpoint (EP0) handler |
| `gateware/usb/usb2/endpoint.py` | Ported | Endpoint multiplexer |
| `gateware/usb/usb2/packet.py` | Ported | Packet generators/detectors |
| `gateware/usb/usb2/reset.py` | Ported | USB reset sequencer |
| `gateware/usb/usb2/transfer.py` | Ported | Transfer state machines |
| `gateware/usb/usb2/descriptor.py` | Ported | Descriptor generation |
| `gateware/usb/usb2/request.py` | Ported | Request handling base |
| `gateware/usb/stream.py` | Ported | USB stream interfaces |

### PHY Interfaces

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/interface/utmi.py` | Ported | UTMI interface |
| `gateware/interface/ulpi.py` | Ported | ULPI interface & translator |
| `gateware/interface/gateware_phy/phy.py` | Ported | Pure gateware PHY |
| `gateware/interface/gateware_phy/transmitter.py` | Ported | PHY transmitter |
| `gateware/interface/gateware_phy/receiver.py` | Ported | PHY receiver |

### Endpoints

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/usb2/endpoints/stream.py` | Ported | Bulk stream endpoints |
| `gateware/usb/usb2/endpoints/isochronous.py` | Ported | Isochronous endpoint |
| `gateware/usb/usb2/endpoints/isochronous_stream_in.py` | Ported | Isochronous IN stream |
| `gateware/usb/usb2/endpoints/isochronous_stream_out.py` | Ported | Isochronous OUT stream |
| `gateware/usb/usb2/endpoints/status.py` | Ported | Status endpoint |

### Request Handlers

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/request/interface.py` | Ported | Request interface definitions |
| `gateware/usb/request/standard.py` | Ported | Standard request handler |
| `gateware/usb/request/control.py` | Ported | Control request base class |

### Utilities

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/utils/bus.py` | Ported | Bus utilities (multiplexers) |
| `gateware/utils/cdc.py` | Ported | Clock domain crossing |
| `gateware/utils/io.py` | Ported | I/O utilities |
| `gateware/memory/__init__.py` | Ported | Memory utilities |
| `gateware/stream/arbiter.py` | Ported | Stream arbiters |
| `gateware/stream/generator.py` | Ported | Stream generators |

### Device Implementations

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/devices/acm.py` | Ported | CDC ACM serial device |

## Key Porting Fixes

The following fixes were applied during the port to resolve behavioral differences between Amaranth and Migen:

### 1. Clock Domains on FSMs

Several FSMs were missing the `usb` clock domain rename, causing them to run in the default `sys` domain instead of the USB clock domain.

**Files fixed:**
- `gateware/usb/usb2/control.py` — `ClockDomainsRenamer("usb")` on the control endpoint FSM
- `gateware/usb/usb2/endpoints/status.py` — `ClockDomainsRenamer("usb")` on the status endpoint FSM
- `gateware/interface/ulpi.py` — `ClockDomainsRenamer("usb")` on the `ULPIRegisterWindow` and `ULPITransmitTranslator` FSMs
- `gateware/usb/usb2/transfer.py` — `ClockDomainsRenamer("usb")` on the `USBInTransferManager` FSM

### 2. UTMI Translator PHY Clock Input

The `UTMITranslator` was not correctly wiring the ULPI clock input to the raw clock domain. When the ULPI record's `clk` field is an input (`clk.i`), the clock domain must be driven from the PHY:

```python
elif hasattr(self.ulpi.clk, 'i'):
    self.comb += ClockSignal(raw_clock_domain).eq(self.ulpi.clk.i)
```

**File:** `gateware/interface/ulpi.py`

### 3. Combinational `new_frame` in USBDevice

`USBDevice.new_frame` was registered (synchronous), which delayed the frame-change signal by one cycle. This caused the microframe counter to miss updates. Changed to combinational so the counter sees the new-frame strobe in the same cycle:

```python
self.comb += self.new_frame.eq(
    token_detector.interface.new_frame &
    (token_detector.interface.frame != self.frame_number)
)
```

**File:** `gateware/usb/usb2/device.py`

### 4. Endpoint Multiplexer Fixes

Three issues were fixed in `USBEndpointMultiplexer`:

- **`past_valid` clock domain**: `past_valid` was sampled in `sys` instead of `usb`, causing the PID-toggle select to lag by a domain crossing. Now sampled in `usb`.
- **PID-toggle priority chain**: The PID-toggle select now uses an `If/Elif` priority chain instead of a `Case` with overlapping conditions, so the first valid interface wins.
- **Idle output gating**: `_multiplex_signals` now gates its `Case` with `~_mux_enc.n`, so shared outputs are not driven from interface 0 when no interface is active.

**File:** `gateware/usb/usb2/endpoint.py`

### 5. Synchronous `ulpi_out_req` in ULPITransmitTranslator

`ULPITransmitTranslator.ulpi_out_req` was driven combinationally. Moved to the `usb` sync domain to match LUNA's timing, where `ulpi_out_req` is set when entering the TRANSMIT state and cleared when `tx_valid` drops:

```python
self.sync.usb += [
    If(fsm.ongoing("IDLE") & self.tx_valid & self.bus_idle,
        self.ulpi_out_req.eq(1)
    ).Elif(fsm.ongoing("TRANSMIT") & ~self.tx_valid,
        self.ulpi_out_req.eq(0)
    )
]
```

**File:** `gateware/interface/ulpi.py`

### 6. USBInTransferManager — ZLP Stream-Ended Flag

The `USBInTransferManager` in `transfer.py` had a subtle issue where the `stream_ended` flag could be written to the wrong double-buffer when `buffer_toggle` and the flag update occurred in the same clock cycle. This was caused by Migen's simulator evaluating sync blocks that assign to the same `Array`-indexed signal in an order that let the clear (for the new write buffer) override the set (for the buffer being sent).

The fix restructures the stream-ended flag handling so that the set and clear do not conflict:
- The fill-count increment and stream-ended set use `buffer_write.we` (the unified write-enable), matching the LUNA original.
- The `read_stream_ended.eq(0)` clears in `WAIT_FOR_DATA` and `WAIT_FOR_ACK` are kept in separate sync blocks gated by `fsm.ongoing(...)`, which works correctly because they target the read buffer (the buffer being swapped away from), not the write buffer.
- All sync blocks use the `usb` clock domain.

**File:** `gateware/usb/usb2/transfer.py`

### 7. Dead `elaborate()` in `StandardRequestHandler`

`StandardRequestHandler` kept the Amaranth `def elaborate(self, platform)` signature. Migen only ever calls `do_finalize()` (no arguments), so the **entire handler body never executed**: no descriptor handler, no FSM, and `interface.claim` was never driven. The request multiplexer therefore fell back to `StallOnlyRequestHandler` and every control request was STALLed — enumeration was impossible. The handler FSM was also missing its `ClockDomainsRenamer("usb")`.

**Fix:** rename to `do_finalize(self)` and wrap the FSM in `ClockDomainsRenamer("usb")`.

**File:** `gateware/usb/request/standard.py`

### 8. No-op helper methods in `ControlRequestHandler`

`handle_register_write_request` and `handle_simple_data_request` built `If(...)` expressions into tuples but never added them to the module nor returned them. In Amaranth these helpers mutate the module via context managers; the port dropped the statements silently. `SET_ADDRESS`, `SET_CONFIGURATION`, `GET_STATUS` and `GET_CONFIGURATION` were empty states.

**Fix:** both helpers now `return [...]` statement lists, spliced into the enclosing `fsm.act(...)` call.

**File:** `gateware/usb/request/control.py`

### 9. Descriptor ROM read port in the wrong clock domain

`Memory.get_port()` defaults to `clock_domain="sys"`, registering `dat_r` on the system clock while all descriptor logic runs in the `usb` domain. With `sys != usb` (e.g. 50MHz vs 60MHz) descriptor data is corrupted. Simulation never caught it because the test bench uses a single clock.

**Fix:** `self.rom.get_port(clock_domain="usb")` in both `USBDescriptorStreamGenerator` and `GetDescriptorHandlerBlock`.

**File:** `gateware/usb/usb2/descriptor.py`

### 10. `StreamSerializer` FSM domain and ArrayProxy payload

Two issues in `gateware/stream/generator.py`:

- The FSM was never renamed to the module's `domain` parameter (LUNA wraps the whole module in `DomainRenamer({"sync": self.domain})`), so it always ran in `sys`. Fixed with `ClockDomainsRenamer(self.domain)`.
- `self.data[position_in_stream]` (an `ArrayProxy`) cannot be lowered by the LiteX verilog backend (`_Part` / proxy nodes are unsupported). Replaced with an explicit `If/Elif` mux chain.

**File:** `gateware/stream/generator.py`

### 11. Variable part-select `.part()` unsupported by LiteX backend

The descriptor byte-select used `dat_r.part((3 - byte_in_stream) * 8, 8)` (Amaranth `word_select` equivalent). The LiteX verilog generator cannot emit `_Part` nodes. Replaced with a fixed 4-way `If/Elif` mux over `byte_in_stream`.

**File:** `gateware/usb/usb2/descriptor.py`

### 12. Miscellaneous protocol fixes

- **Handshake generator priority** (`gateware/usb/usb2/packet.py`): `If/Elif/Elif` inverted LUNA's priority; restored three independent `If` statements so the last match wins (STALL > NAK > ACK).
- **Setup packet recipient slice** (`gateware/usb/usb2/request.py`): `packet[0][0:2]` truncated the 5-bit recipient field; corrected to `[0:5]`.
- **Combinational `new_frame`** (`gateware/usb/device.py`): the fix from section 3 had only been applied to `usb2/device.py`; the (live, duplicated) top-level `usb/device.py` still used the registered version. Now combinational in both.

## Hardware Validation

Validated on a **Terasic DECA (MAX10) + TUSB1210 ULPI PHY** target (`terasic_deca_counter.py`): full-speed connect, high-speed chirp, control-endpoint enumeration (`1209:0001`) and bulk-IN streaming all work.

Integration note: the PHY's 60MHz CLOCK output only starts once it sees REFCLK (driven by the FPGA's usb clock). Gating the usb domain reset on PLL lock (LiteX `create_clkout(..., with_reset=True)` default) therefore **deadlocks**: the PHY is held in reset and the clock loop never bootstraps. Create the usb clock with `with_reset=False` and leave the PHY reset line deasserted at startup, as LUNA does.

For board-level debugging, an `altsource_probe` (In-System Sources & Probes) instance reading PLL-lock/ULPI/UTMI state over JTAG proved invaluable (note: `jtag_uart` and ISSP cannot share the JTAG debug hub — use `uart_name="stub"`).

## Key Changes from Amaranth to Migen

### Import Statements
```python
# Amaranth
from amaranth import Signal, Module, Elaboratable
from amaranth.hdl.rec import Record, DIR_FANIN, DIR_FANOUT

# Migen
from migen import *
from migen.genlib.record import Record
```

### Module Definition
```python
# Amaranth
class MyModule(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        m.d.comb += signal.eq(value)
        return m

# Migen
class MyModule(Module):
    def __init__(self):
        self.comb += signal.eq(value)
```

### FSM Definition
```python
# Amaranth
with m.FSM(domain="usb"):
    with m.State("IDLE"):
        m.next = "ACTIVE"

# Migen
self.submodules.fsm = fsm = FSM(reset_state="IDLE")
fsm.act("IDLE", NextState("ACTIVE"))
```

### Clock Domains
```python
# Amaranth
m.d.usb += signal.eq(value)  # usb domain
m.d.sync += signal.eq(value)  # sync domain

# Migen
self.sync.usb += signal.eq(value)  # usb domain
self.sync += signal.eq(value)      # sync domain
```

### Records
```python
# Amaranth
Record([('field', 8, DIR_FANOUT)])

# Migen
Record([('field', 8)])
```

### Memory
```python
# Amaranth
m.submodules.mem = mem = Memory(shape=8, depth=64, init=[])
write_port = mem.write_port(domain="usb")

# Migen
m.submodules.mem = mem = Memory(8, 64, init=[])
write_port = mem.get_write_port(clock_domain="usb")
```

### Array Indexing
```python
# Amaranth — Array proxy reads and writes are atomic within a sync statement
write_stream_ended = buffer_stream_ended[write_buffer_number]
m.d.usb += write_stream_ended.eq(1)  # writes to the selected element

# Migen — Array proxy works but care is needed when the index signal
# is also assigned in the same sync block. Separate the set and clear
# into different sync blocks or use explicit If/Else branches.
```

## Dependencies

- `migen` — Hardware description library
- `litex` — SoC builder framework
- `usb-protocol` — USB protocol constants and descriptors

## Package Structure

```
liteusb/
├── liteusb/
│   ├── gateware/
│   │   ├── usb/          # USB device stack
│   │   ├── interface/    # PHY interfaces
│   │   ├── utils/        # Utilities
│   │   ├── stream/       # Stream interfaces
│   │   └── memory/       # Memory utilities
│   └── __init__.py
├── liteusb/tests/        # Test suite (run via liteusb.tests.*)
├── tests/                # Legacy test copies (not used by default)
├── examples/             # Usage examples
├── setup.py              # Package setup
├── run_tests.py          # Custom test runner
├── README.md             # Documentation
├── TEST_REPORT.md        # Historical test report
└── PORTING_SUMMARY.md    # This file
```

## Running Tests

```bash
# Run the full suite via the custom runner (also runs the
# device-level tests the plain unittest discovery misses)
python3 run_tests.py

# Or run modules individually
python3 -m unittest liteusb.tests.test_usb2_packet liteusb.tests.test_usb2_endpoints \
  liteusb.tests.test_usb2_descriptor liteusb.tests.test_usb2_transfer \
  liteusb.tests.test_usb2_request liteusb.tests.test_usb2_reset \
  liteusb.tests.test_usb_stream liteusb.tests.test_ulpi -v

# Run a single test module
python3 -m unittest liteusb.tests.test_usb2_transfer -v

# Run a single test
python3 -m unittest liteusb.tests.test_usb2_transfer.USBInTransferManagerTest.test_zlp_generation -v

# Generate VCD files for debugging
GENERATE_VCDS=1 python3 -m unittest liteusb.tests.test_usb2_packet -v
```

## Known Issues

1. **`test_usb2_device`** — The full device enumeration tests (`test_enumeration`, `test_long_descriptor`, `test_descriptor_zlp`) are not discoverable by the standard unittest runner due to the custom test harness structure. They require the `run_tests.py` script or manual invocation.

2. **Migen Array proxy in sync** — When a signal used as an `Array` index is also assigned in the same sync block, Migen's simulator may evaluate the index using the post-assignment value. The `USBInTransferManager` works around this by keeping set and clear operations in separate sync blocks that target different buffers.

3. **VCD generation** — Requires the `GENERATE_VCDS` environment variable to be set.

4. **Simulation speed** — Migen's simulator is slower than Amaranth's.

## Credits

- Original LUNA project by Great Scott Gadgets
- Ported to Migen/LiteX by Hans Baier and contributors
- USB protocol constants from usb-protocol package

## License

BSD-3-Clause (same as original LUNA project)
