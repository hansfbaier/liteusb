# LiteUSB

```
              Copyright (c) 2020-2024 Great Scott Gadgets
              Copyright (c) 2025-2026 Hans Baier
```

![License](https://img.shields.io/badge/License-BSD%203--Clause-orange.svg)

## Overview

**LiteUSB** is a small footprint and configurable USB device gateware library, originally developed as a port of [LUNA](https://github.com/greatscottgadgets/luna) from Amaranth HDL to Migen/LiteX. It provides a complete USB 2.0 device stack for FPGA designs, enabling easy integration of USB functionality into System-on-Chip (SoC) designs.

### Key Features

- :heavy_check_mark: **USB 2.0 Device Support**: Full-speed (12 Mbps) and high-speed (480 Mbps) operation
- :heavy_check_mark: **Multiple PHY Interfaces**: ULPI and UTMI PHY support
- :heavy_check_mark: **LiteX Integration**: Native integration with LiteX SoC framework
- :heavy_check_mark: **Migen-based**: Uses Migen for hardware description
- :heavy_check_mark: **Configurable**: Modular design allows selecting only needed components
- :heavy_check_mark: **Standard Compliant**: Standard USB request handlers and descriptors
- :heavy_check_mark: **Endpoint Support**: Control, Bulk, Interrupt, and Isochronous endpoints
- :heavy_check_mark: **Simulation Support**: Tested and verifiable with simulation

### Architecture

```
                    +------------------+
                    |   USB Host       |
                    +--------+---------+
                             |
                    +--------v---------+
                    |    ULPI/UTMI     |
                    |      PHY         |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   LiteUSB Core   |
                    |  - USB Device    |
                    |  - Endpoints     |
                    |  - Descriptors   |
                    +--------+---------+
                             |
            +----------------+----------------+
            |                |                |
    +-------v-------+ +------v------+ +------v------+
    |   Control     | |   Bulk      | |  Interrupt  |
    |   Endpoint    | |  Endpoints  | |  Endpoints  |
    |   (EP0)       | |             | |             |
    +---------------+ +-------------+ +-------------+
```

## Installation

### Prerequisites

- Python 3.7+
- Migen
- LiteX
- usb-protocol

### Install from Source

```bash
# Clone the repository
git clone https://github.com/hansfbaier/liteusb.git
cd liteusb

# Install dependencies and liteusb
pip install -e .

# Or install directly from PyPI (when available)
pip install liteusb
```

### Using LiteX Setup Script

If you're using the LiteX ecosystem, the recommended way to install is via the litex_setup script:

```bash
# Download and run the LiteX setup script
wget https://raw.githubusercontent.com/enjoy-digital/litex/master/litex_setup.py
chmod +x litex_setup.py
./litex_setup.py --init --install --user
```

## Quick Start

### Build the Examples

All examples can be built to Verilog using `--build`:

```bash
python examples/simple_device.py --build
python examples/counter_device.py --build
python examples/vendor_request.py --build
python examples/stream_out_device.py --build
python examples/interrupt_device.py --build
python examples/isochronous_count.py --build
python examples/stress_test_device.py --build
python examples/acm_serial.py --build
```

### Verified on Hardware

LiteUSB was validated on real hardware on the **Terasic DECA** board
(Intel MAX10 FPGA + TUSB1210 ULPI PHY), Linux host:

- `counter_device.py` — enumerates and streams the bulk-IN monotonic
  counter; verified with `examples/test_counter_device.py`.
- `interrupt_device.py` — enumerates and delivers interrupt-IN packets;
  verified with `examples/test_interrupt_device.py`.
- `simple_device.py` — enumerates (descriptors, strings, EP0 control);
  verified with `examples/test_simple_device.py`, observed at High Speed.

High Speed (chirp) is not yet reliable across examples/bitstreams —
a device that comes up at Full Speed remains usable at FS. A known
open issue: after ~2 s of bus idle the host autosuspends the device
and resume does not recover (all further transfers fail) until replug.

All examples can be built as DECA bitstreams with the `--deca` flag.
Hardware-specific code (clocking, ULPI hookup, diagnostic LEDs)
is factored out into [examples/terasic_deca_common.py](examples/terasic_deca_common.py):

```bash
python examples/counter_device.py       --deca --cpu-type=None --build
python examples/simple_device.py        --deca --cpu-type=None --build
python examples/vendor_request.py       --deca --cpu-type=None --build
python examples/stream_out_device.py    --deca --cpu-type=None --build
python examples/interrupt_device.py     --deca --cpu-type=None --build
python examples/isochronous_count.py    --deca --cpu-type=None --build
python examples/stress_test_device.py   --deca --cpu-type=None --build
python examples/acm_serial.py           --deca --cpu-type=None --build
# load build/terasic_deca/gateware/terasic_deca.sof via JTAG (quartus_pgm)
$ lsusb -d 1209:0001
Bus 005 Device 023: ID 1209:0001 Generic pid.codes Test PID
```

The shared target also provides `--debug-leds` (sticky diagnostic LEDs).
A `--with-issp` flag (In-System Sources & Probes readout of the USB
state over JTAG) exists but is currently **broken on the MAX10**: with
`altsource_probe` instantiated the usb PLL never locks, for unknown
reasons — do not use it. Note the PHY clocking gotcha documented in
`terasic_deca_common.py`: the usb clock domain must be created with
`with_reset=False`, otherwise the PLL-lock-gated reset holds the PHY in
reset and the clock loop never starts.

### Basic Usage

See [examples/simple_device.py](examples/simple_device.py) for the complete working example:

```python
from migen import *
from liteusb import USBDevice, UTMIInterface
from usb_protocol.emitters import DeviceDescriptorCollection

class MyUSBDevice(Module):
    def __init__(self, phy):
        self.submodules.usb = usb = USBDevice(bus=phy)

        # Create descriptors
        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor      = 0x1209
            d.idProduct     = 0x0001
            d.bcdDevice     = 1.00
            d.iManufacturer = "LiteUSB"
            d.iProduct      = "My Device"
            d.iSerialNumber = "0001"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x01
                    e.wMaxPacketSize   = 512

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81
                    e.wMaxPacketSize   = 512

        # Add control endpoint
        usb.add_standard_control_endpoint(descriptors)

        # Connect device
        self.comb += usb.connect.eq(1)
```

### Integration with LiteX Platform

```python
from litex_boards.platforms import my_platform
from litex.build.generic_platform import Pins, Subsignal, IOStandard

# Create platform
platform = my_platform.Platform()

# Add USB PHY resource (if not already defined)
platform.add_extension([
    ("usb", 0,
        Subsignal("clk", Pins("A1")),
        Subsignal("stp", Pins("B1")),
        Subsignal("dir", Pins("C1")),
        Subsignal("nxt", Pins("D1")),
        Subsignal("data", Pins("E1 E2 E3 E4 E5 E6 E7 E8")),
        IOStandard("LVCMOS33"),
    ),
])

# Build the design
from litex.soc.integration.builder import Builder
builder = Builder(soc)
builder.build()
```

## Available Modules

### Core USB Stack

| Module | Description |
|--------|-------------|
| `liteusb.gateware.usb.device` | Main USB device class (`USBDevice`) |
| `liteusb.gateware.usb.usb2.device` | USB 2.0 device implementation |
| `liteusb.gateware.usb.usb2.control` | Control endpoint (EP0) |
| `liteusb.gateware.usb.usb2.endpoint` | Endpoint multiplexer |
| `liteusb.gateware.usb.usb2.packet` | Packet generators, detectors, tokenizers, CRCs |
| `liteusb.gateware.usb.usb2.reset` | USB reset sequencer |
| `liteusb.gateware.usb.usb2.transfer` | Transfer state machines |
| `liteusb.gateware.usb.usb2.descriptor` | Descriptor generation (ROM-based) |
| `liteusb.gateware.usb.stream` | USB stream interfaces |

### Endpoint Types

| Module | Description |
|--------|-------------|
| `liteusb.gateware.usb.usb2.endpoints.stream` | Bulk/Interrupt stream endpoints (IN and OUT) |
| `liteusb.gateware.usb.usb2.endpoints.isochronous` | Isochronous memory-mapped IN endpoint |
| `liteusb.gateware.usb.usb2.endpoints.isochronous_stream_in` | Isochronous stream IN endpoint |
| `liteusb.gateware.usb.usb2.endpoints.isochronous_stream_out` | Isochronous stream OUT endpoint |
| `liteusb.gateware.usb.usb2.endpoints.status` | Signal/status IN endpoint (interrupt) |

### PHY Interfaces

| Module | Description |
|--------|-------------|
| `liteusb.gateware.interface.ulpi` | ULPI PHY interface and translator |
| `liteusb.gateware.interface.utmi` | UTMI/UTMI+ interface (simulation aid) |
| `liteusb.gateware.interface.gateware_phy` | Pure gateware USB PHY (transmitter/receiver) |

### Request Handlers

| Module | Description |
|--------|-------------|
| `liteusb.gateware.usb.request.standard` | Standard USB request handler |
| `liteusb.gateware.usb.request.control` | Control request handler base class |
| `liteusb.gateware.usb.request.interface` | Request handler interface definitions |
| `liteusb.gateware.usb.usb2.request` | Setup decoder, request handler multiplexer |

### Devices

| Module | Description |
|--------|-------------|
| `liteusb.gateware.usb.devices.acm` | CDC-ACM USB serial device |

### Utilities & Infrastructure

| Module | Description |
|--------|-------------|
| `liteusb.gateware.utils.cdc` | Clock domain crossing |
| `liteusb.gateware.utils.bus` | Bus utilities (multiplexers) |
| `liteusb.gateware.utils.io` | I/O utilities |
| `liteusb.gateware.stream` | Stream interface definitions |
| `liteusb.gateware.stream.generator` | StreamSerializer for data streaming |
| `liteusb.gateware.stream.arbiter` | Stream arbiters |
| `liteusb.gateware.memory` | Transactionalized FIFO |

## Documentation

For more detailed documentation, examples, and API reference, see the source tree and example files in [examples/](examples/).

### Related Projects

- [LUNA](https://github.com/greatscottgadgets/luna) - Original Amaranth-based USB library
- [LiteX](https://github.com/enjoy-digital/litex) - SoC builder framework
- [Migen](https://github.com/m-labs/migen) - Python-based HDL

## License

This project is licensed under the **BSD 3-Clause License**. See the LICENSE file for details.

```
Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
Copyright (c) 2025-2026 Hans Baier <foss@hans-baier.de>

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Contact

- **Issues**: [GitHub Issues](https://github.com/hansfbaier/liteusb/issues)
- **Email**: foss@hans-baier.de
