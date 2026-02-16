# LiteUSB Test Suite

This directory contains comprehensive unit tests for LiteUSB gateware components. The tests use gateware simulation to verify USB protocol implementations, from low-level packet handling to full device enumeration.

## Overview

The LiteUSB test suite provides:

- **Gateware simulation tests** using migen's simulation framework
- **USB 2.0 protocol testing** from packets to full device enumeration
- **Hardware interface testing** (UTMI, ULPI)
- **Component-level unit tests** for individual gateware modules
- **Integration tests** for complete USB device behavior

All tests are written using Python's `unittest` framework with custom migen-based simulation harnesses.

## Quick Start

### Run All Tests

```bash
# From the liteusb directory
python run_tests.py

# With verbose output
python run_tests.py -v
```

### Run Specific Test File

```bash
# Run only packet tests
python run_tests.py usb2_packet

# Run only ULPI tests
python run_tests.py ulpi
```

### Run Individual Test Class or Method

```bash
# Using pytest (install pytest first: pip install pytest)
cd /path/to/liteusb
pytest tests/test_usb2_packet.py -v

# Run specific test class
pytest tests/test_usb2_packet.py::USBTokenDetectorTest -v

# Run specific test method
pytest tests/test_usb2_packet.py::USBTokenDetectorTest::test_valid_token -v
```

### Using unittest directly

```bash
python -m unittest tests.test_usb2_packet -v

# Run specific test
python -m unittest tests.test_usb2_packet.USBTokenDetectorTest.test_valid_token -v
```

## Test Organization

| Test File | Coverage |
|-----------|----------|
| `test_usb2_packet.py` | USB 2.0 packet-level components: TokenDetector, HandshakeDetector, DataPacketReceiver, DataPacketGenerator, DataPacketDeserializer, InterpacketTimer |
| `test_usb2_request.py` | USB setup request handling, SETUP transaction decoding |
| `test_usb2_descriptor.py` | USB descriptor handling, GetDescriptor request processing |
| `test_usb2_endpoints.py` | USB endpoint implementations (Isochronous IN/OUT) |
| `test_usb2_transfer.py` | USB transfer-level operations and state machines |
| `test_usb2_device.py` | Full USB device enumeration and control transfers |
| `test_usb2_reset.py` | USB bus reset detection and handling |
| `test_ulpi.py` | ULPI PHY interface: register access, control translation, receive event decoding, transmit translation |
| `test_usb_stream.py` | USB stream processing components |

### Test Categories

**Packet-Level Tests** (`test_usb2_packet.py`)
- Token detection (IN, OUT, SETUP, SOF)
- Handshake packet generation/detection (ACK, NAK, STALL, NYET)
- Data packet serialization/deserialization
- CRC generation and validation
- Interpacket timing

**Request-Level Tests** (`test_usb2_request.py`)
- SETUP transaction decoding
- Request type parsing (bmRequestType, bRequest, wValue, wIndex, wLength)
- Speed-dependent timing (Full Speed vs High Speed)

**Descriptor Tests** (`test_usb2_descriptor.py`)
- Device descriptor retrieval
- Configuration descriptor handling
- String descriptor support
- Partial descriptor reads
- Stall behavior for invalid descriptors

**Endpoint Tests** (`test_usb2_endpoints.py`)
- Isochronous endpoint operation
- Stream-based data transfer
- Packet boundary handling

**Device-Level Tests** (`test_usb2_device.py`)
- Complete enumeration sequences
- Standard control endpoint
- SET_ADDRESS handling
- SET_CONFIGURATION handling
- Descriptor enumeration

**PHY Interface Tests** (`test_ulpi.py`)
- Register read/write operations
- Bus arbitration with DIR signal
- RX event command decoding
- Transmit packet translation

## Test Infrastructure

### Key Files

- `utils.py` - Base test classes and decorators
- `usb2.py` - USB device test harness with transaction helpers
- `usb_packet.py` - USB packet construction utilities

### Base Test Classes

**`LiteUSBTestCase`** (`utils.py`)
Base class for all gateware tests. Provides:
- Automatic clock domain setup
- Signal initialization hooks
- Simulation control with VCD generation support
- Helper methods: `advance_cycles()`, `wait_until()`, `pulse()`

```python
class MyTest(LiteUSBTestCase):
    SYNC_CLOCK_FREQUENCY = 120e6  # or None
    USB_CLOCK_FREQUENCY = 60e6    # or None
    FAST_CLOCK_FREQUENCY = None   # or value
    
    FRAGMENT_UNDER_TEST = MyModule
    FRAGMENT_ARGUMENTS = {'param': value}
    
    def initialize_signals(self):
        # Set initial signal values
        yield self.dut.enable.eq(1)
```

**`LiteUSBUSBTestCase`** (`utils.py`)
Specialized for USB-domain only tests (no sync domain).

**`USBDeviceTest`** (`usb2.py`)
Full-device test harness providing:
- UTMI bus interface
- High-level transaction methods
- Enumeration helpers

### Decorators

**`@usb_domain_test_case`**
Marks a test method to run in the USB clock domain (60 MHz).

```python
@usb_domain_test_case
def test_my_feature(self):
    yield from self.advance_cycles(10)
    self.assertEqual((yield self.dut.status), 1)
```

**`@sync_test_case`**
Marks a test method to run in the sync clock domain.

## How to Add New Tests

### 1. Simple Component Test

```python
from liteusb.tests import LiteUSBUSBTestCase, usb_domain_test_case
from mymodule import MyGateware

class MyGatewareTest(LiteUSBUSBTestCase):
    FRAGMENT_UNDER_TEST = MyGateware
    FRAGMENT_ARGUMENTS = {'param': 'value'}
    
    def initialize_signals(self):
        # Set initial signal states
        yield self.dut.reset.eq(0)
    
    @usb_domain_test_case
    def test_basic_operation(self):
        # Test code here
        yield from self.advance_cycles(5)
        self.assertEqual((yield self.dut.output), expected_value)
```

### 2. USB Device Test

```python
from liteusb.tests.usb2 import USBDeviceTest
from mydevice import MyUSBDevice

class MyDeviceTest(USBDeviceTest):
    FRAGMENT_UNDER_TEST = MyUSBDevice
    
    def initialize_signals(self):
        yield self.utmi.line_state.eq(0b01)  # Prevent reset
        yield self.dut.connect.eq(1)
        yield self.utmi.tx_ready.eq(1)
    
    def provision_dut(self, dut):
        # Add endpoints, descriptors, etc.
        dut.add_standard_control_endpoint(descriptors)
    
    def test_custom_feature(self):
        # Use built-in transaction helpers
        handshake, data = yield from self.control_request_in(...)
        self.assertEqual(handshake, USBPacketID.ACK)
```

### 3. Test File Guidelines

- Name test files `test_<feature>.py`
- Import from `liteusb.tests` for base classes
- Use descriptive test method names: `test_<what>_<condition>_<expected_result>`
- Include docstrings explaining what the test validates
- Use `yield from self.advance_cycles(n)` for timing
- Use `self.assertEqual()` for assertions

### 4. Running Your New Tests

```bash
# Run your new test file
python run_tests.py my_feature

# With verbose output
python run_tests.py my_feature -v

# Using pytest
pytest tests/test_my_feature.py -v
```

## Environment Variables

- `GENERATE_VCDS=1` - Generate VCD waveform files for debugging (saved in current directory)

```bash
GENERATE_VCDS=1 python run_tests.py usb2_packet -v
```

## Troubleshooting

### Import Errors

If you get import errors, ensure you're running from the correct directory:

```bash
cd /path/to/liteusb
python run_tests.py
```

The `run_tests.py` script automatically sets up the Python path for dependencies (migen, litex, python-usb-protocol).

### Simulation Hangs

If a test hangs, it may be waiting for a signal that never arrives:
- Check that all required signals are initialized
- Verify clock domains are properly configured
- Look for missing `yield` statements in your test

### VCD Generation

To debug timing issues, enable VCD generation:
```bash
GENERATE_VCDS=1 python run_tests.py <test_name>
```

Then open the `.vcd` file in GTKWave or similar.

## Known Limitations

1. **Simulation Speed**: Gateware simulation is slower than software-only tests. Some device-level tests may take several seconds to complete.

2. **Clock Domain Crossing**: Tests must properly configure clock domains. A common mistake is using `usb_domain_test_case` without setting `USB_CLOCK_FREQUENCY`.

3. **UTMI Model**: The UTMI interface model is simplified and doesn't capture all PHY behaviors.

4. **Timing Precision**: Simulation uses discrete time steps; actual hardware timing may vary slightly.

5. **External Dependencies**: Tests require the full LiteUSB dependency tree (migen, litex, python-usb-protocol).

## Examples

### Running Tests with Different Options

```bash
# Run all tests with verbose output
python run_tests.py -v

# Stop on first failure
python run_tests.py --failfast

# Run with short traceback
python run_tests.py --tb=short

# Run only USB 2.0 related tests using pytest
pytest tests/test_usb2_*.py -v

# Run tests matching a pattern
pytest tests/ -k "token" -v

# Skip slow tests
pytest tests/ -m "not slow" -v
```

### Continuous Integration

For CI environments, use the failfast option:
```bash
python run_tests.py --failfast -v
```

This ensures quick feedback if any test fails.

## Further Reading

- [Migen documentation](https://m-labs.hk/migen/manual/)
- [USB 2.0 Specification](https://usb.org/sites/default/files/usb_20_20210701.zip)
- [ULPI Specification](https://www.sparkfun.com/datasheets/Components/SMD/ULPI_v1_1.pdf)

## Contributing

When adding new features:
1. Add corresponding tests in the appropriate `test_*.py` file
2. Ensure all tests pass: `python run_tests.py`
3. For bug fixes, add a regression test that fails before the fix
4. Update this README if you add new test categories or utilities
