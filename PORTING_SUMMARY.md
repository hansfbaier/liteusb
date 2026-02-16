# LiteUSB Porting Summary

## Overview

This document summarizes the porting of LUNA (USB FPGA gateware) from Amaranth HDL to Migen/LiteX framework.

## Statistics

- **Total Python files**: 60
- **Gateware modules**: 39
- **Test files**: 15
- **Lines of code**: ~20,000+

## Ported Modules

### Core USB Stack

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/device.py` | ✅ Ported | Main USB device controller |
| `gateware/usb/usb2/device.py` | ✅ Ported | USB2 device implementation |
| `gateware/usb/usb2/control.py` | ✅ Ported | Control endpoint (EP0) handler |
| `gateware/usb/usb2/endpoint.py` | ✅ Ported | Endpoint multiplexer |
| `gateware/usb/usb2/packet.py` | ✅ Ported | Packet generators/detectors |
| `gateware/usb/usb2/reset.py` | ✅ Ported | USB reset sequencer |
| `gateware/usb/usb2/transfer.py` | ✅ Ported | Transfer state machines |
| `gateware/usb/usb2/descriptor.py` | ✅ Ported | Descriptor generation |
| `gateware/usb/usb2/request.py` | ✅ Ported | Request handling base |
| `gateware/usb/stream.py` | ✅ Ported | USB stream interfaces |

### PHY Interfaces

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/interface/utmi.py` | ✅ Ported | UTMI interface |
| `gateware/interface/ulpi.py` | ✅ Ported | ULPI interface & translator |
| `gateware/interface/gateware_phy/phy.py` | ✅ Ported | Pure gateware PHY |
| `gateware/interface/gateware_phy/transmitter.py` | ✅ Ported | PHY transmitter |
| `gateware/interface/gateware_phy/receiver.py` | ✅ Ported | PHY receiver |

### Endpoints

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/usb2/endpoints/stream.py` | ✅ Ported | Bulk stream endpoints |
| `gateware/usb/usb2/endpoints/isochronous.py` | ✅ Ported | Isochronous endpoint |
| `gateware/usb/usb2/endpoints/isochronous_stream_in.py` | ✅ Ported | Isochronous IN stream |
| `gateware/usb/usb2/endpoints/isochronous_stream_out.py` | ✅ Ported | Isochronous OUT stream |
| `gateware/usb/usb2/endpoints/status.py` | ✅ Ported | Status endpoint |

### Request Handlers

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/request/interface.py` | ✅ Ported | Request interface definitions |
| `gateware/usb/request/standard.py` | ✅ Ported | Standard request handler |
| `gateware/usb/request/control.py` | ✅ Ported | Control request base class |

### Utilities

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/utils/bus.py` | ✅ Ported | Bus utilities (multiplexers) |
| `gateware/utils/cdc.py` | ✅ Ported | Clock domain crossing |
| `gateware/utils/io.py` | ✅ Ported | I/O utilities |
| `gateware/memory/__init__.py` | ✅ Ported | Memory utilities |
| `gateware/stream/arbiter.py` | ✅ Ported | Stream arbiters |
| `gateware/stream/generator.py` | ✅ Ported | Stream generators |

### Device Implementations

| Module | Status | Description |
|--------|--------|-------------|
| `gateware/usb/devices/acm.py` | ✅ Ported | CDC ACM serial device |

## Test Suite

### Test Files Ported

| Test File | Description |
|-----------|-------------|
| `tests/test_usb2_packet.py` | USB packet tests (token, data, handshake) |
| `tests/test_usb2_device.py` | Full device enumeration tests |
| `tests/test_usb2_endpoints.py` | Endpoint functionality tests |
| `tests/test_usb2_descriptor.py` | Descriptor generation tests |
| `tests/test_usb2_transfer.py` | USB transfer tests |
| `tests/test_usb2_request.py` | Request handler tests |
| `tests/test_usb2_reset.py` | Reset sequencer tests |
| `tests/test_usb_stream.py` | Stream interface tests |
| `tests/test_ulpi.py` | ULPI interface tests |

### Test Framework

- `tests/utils.py` - Base test case classes and decorators
- `tests/usb2.py` - USB device test harness
- `tests/usb_packet.py` - USB packet generation utilities

## Key Changes from Amaranth to Migen

### 1. Import Statements
```python
# Amaranth
from amaranth import Signal, Module, Elaboratable
from amaranth.hdl.rec import Record, DIR_FANIN, DIR_FANOUT

# Migen
from migen import *
from migen.genlib.record import Record
```

### 2. Module Definition
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

### 3. FSM Definition
```python
# Amaranth
with m.FSM(domain="usb"):
    with m.State("IDLE"):
        m.next = "ACTIVE"

# Migen
self.submodules.fsm = fsm = FSM(reset_state="IDLE")
fsm.act("IDLE", NextState("ACTIVE"))
```

### 4. Clock Domains
```python
# Amaranth
m.d.usb += signal.eq(value)  # usb domain
m.d.sync += signal.eq(value)  # sync domain

# Migen
self.sync.usb += signal.eq(value)  # usb domain
self.sync += signal.eq(value)      # sync domain
```

### 5. Records
```python
# Amaranth
Record([('field', 8, DIR_FANOUT)])

# Migen
Record([('field', 8)])
```

## Dependencies

- `migen` - Hardware description library
- `litex` - SoC builder framework
- `usb-protocol` - USB protocol constants and descriptors

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
├── tests/                # Test suite
├── examples/             # Usage examples
├── setup.py             # Package setup
├── README.md            # Documentation
└── PORTING_SUMMARY.md   # This file
```

## Running Tests

```bash
# Run all tests
python run_tests.py

# Run specific test
python run_tests.py test_usb2_packet

# Run with pytest
pytest tests/

# Generate VCD files for debugging
GENERATE_VCDS=1 python run_tests.py
```

## Known Issues

1. Some tests may require specific clock domain configurations
2. VCD generation requires the `GENERATE_VCDS` environment variable
3. Simulation speed is slower than with Amaranth (migen simulator is slower)

## Credits

- Original LUNA project by Great Scott Gadgets
- Ported to Migen/LiteX by Claude
- USB protocol constants from usb-protocol package

## License

BSD-3-Clause (same as original LUNA project)