```
    __    _ __       __  _______ ____
   / /   (_) /____  / / / / ___// __ )
  / /   / / __/ _ \/ / / /\__ \/ __  |
 / /___/ / /_/  __/ /_/ /___/ / /_/ /
/_____/_/\__/\___/\____//____/_____/

  Copyright (c) 2020-2024 Great Scott Gadgets
          Copyright (c) 2025-2026 Hans Baier

 Small footprint and configurable USB device cores
          powered by Migen & LiteX
```

[![](https://img.shields.io/badge/License-BSD%203--Clause-orange.svg)](#license)

[> Intro
--------

LiteUSB is a small footprint and configurable USB device gateware library,
originally developed as a port of [LUNA](https://github.com/greatscottgadgets/luna)
from Amaranth HDL to Migen/LiteX. It provides a complete USB 2.0 device stack
for FPGA designs.

LiteUSB is part of the LiteX ecosystem. Using Migen to describe the HDL allows
the core to be highly configurable. It can be used as a LiteX library or
integrated into standard design flows by generating Verilog RTL.

[> Features
-----------

- **USB 2.0 Device**: High-speed (480 Mbps) and full-speed (12 Mbps) operation.
- **Multiple PHY Interfaces**: ULPI (external PHY) and UTMI (simulation).
- **Endpoint Types**: Control, Bulk, Interrupt, and Isochronous.
- **Migen-based**: Pure Migen HDL, no external dependencies.
- **Standard Compliant**: Standard USB request handlers, descriptors, CRCs.
- **Simulation Support**: Full test suite with device-level integration tests.
- **CDC-ACM**: Built-in USB serial device (`liteusb.gateware.usb.devices.acm`).

[> FPGA Proven
--------------

LiteUSB has been verified on the **Terasic DECA** (Intel MAX10 + TUSB1210
ULPI PHY) with a Linux host:

| Example | Description | Verified |
|---------|-------------|----------|
| `counter_device.py` | Bulk-IN monotonic counter | PASS |
| `interrupt_device.py` | Interrupt-IN packets | PASS |
| `simple_device.py` | Enumeration, strings, EP0 | PASS (HS) |
| `stream_out_device.py` | Bulk-OUT → bulk-IN loopback | PASS |
| `vendor_request.py` | Vendor requests, LED control | PASS |
| `acm_serial.py` | CDC-ACM virtual serial loopback | PASS |
| `stress_test_device.py` | Bulk-IN maximum rate streamer | PASS |
| `isochronous_count.py` | Isochronous-IN counter | PASS (HS) |

All eight examples verified on hardware.  Previously open issues
(bulk-OUT corruption, autosuspend, STALL, HS chirp) have been resolved.

[> Architecture
---------------

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

[> Getting started
------------------

1. Install Python 3.7+ and FPGA vendor development tools.
2. Install Migen and LiteX by following the [LiteX installation guide](https://github.com/enjoy-digital/litex/wiki/Installation).
3. Install `usb-protocol`: `pip install usb-protocol`.

Build an example:

```bash
python examples/simple_device.py --build
```

All examples default to **High Speed** (512-byte bulk, 1024-byte isochronous,
3 packets/microframe). Set `LITEUSB_FULL_SPEED=1` for Full Speed (64-byte bulk,
1023-byte isochronous, 1 packet/frame).

### Build for Terasic DECA

All examples can be built as DECA bitstreams with the `--deca` flag.
Hardware-specific code (clocking, ULPI hookup, diagnostic LEDs) is factored
into `examples/terasic_deca_common.py`:

```bash
python examples/counter_device.py       --deca --build
python examples/simple_device.py        --deca --build
python examples/vendor_request.py       --deca --build
python examples/stream_out_device.py    --deca --build
python examples/interrupt_device.py     --deca --build
python examples/isochronous_count.py    --deca --build
python examples/stress_test_device.py   --deca --build
python examples/acm_serial.py           --deca --build
```

Load `build/terasic_deca/gateware/terasic_deca.sof` via JTAG (`quartus_pgm`).

There is also `examples/terasic_deca_soc_acm_console.py` — a full LiteX SoC
(VexRiscv CPU + BIOS) with the console UART tunneled through a USB CDC-ACM
virtual COM port. Open `/dev/ttyACM0` (or `litex_term /dev/ttyACM0`) to get
the LiteX BIOS prompt over USB.

The shared target also provides `--debug-leds` (sticky diagnostic LEDs).

Note the PHY clocking gotcha: the usb clock domain must be created with
`with_reset=False`, otherwise the PLL-lock-gated reset holds the PHY in
reset and the clock loop never starts.

### Basic Usage

```python
from migen import *
from liteusb import USBDevice
from usb_protocol.emitters import DeviceDescriptorCollection

class MyUSBDevice(Module):
    def __init__(self, phy):
        self.submodules.usb = usb = USBDevice(bus=phy)

        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor      = 0x1209
            d.idProduct     = 0x0001
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

        usb.add_standard_control_endpoint(descriptors)
        self.comb += usb.connect.eq(1)
```

### Integration with LiteX Platform

```python
from litex_boards.platforms import my_platform
from litex.build.generic_platform import Pins, Subsignal, IOStandard

platform = my_platform.Platform()
platform.add_extension([
    ("usb", 0,
        Subsignal("clk",  Pins("A1")),
        Subsignal("stp",  Pins("B1")),
        Subsignal("dir",  Pins("C1")),
        Subsignal("nxt",  Pins("D1")),
        Subsignal("data", Pins("E1 E2 E3 E4 E5 E6 E7 E8")),
        IOStandard("LVCMOS33"),
    ),
])

from litex.soc.integration.builder import Builder
builder = Builder(soc)
builder.build()
```

[> Documentation
----------------

- **[Architecture Reference](https://hansfbaier.github.io/liteusb/doc/architecture.html)** — Complete HTML documentation
  with architecture overview, per-module descriptions, USB 2.0 spec quotes, and
  interactive WaveDrom timing diagrams derived from the unit test VCD traces.
  *(Requires [GitHub Pages](https://docs.github.com/en/pages/quickstart) enabled on the repo.
  Or open [`doc/architecture.html`](doc/architecture.html) locally after cloning.)*

  Regenerate the diagrams and HTML after test changes:
  ```bash
  python3 doc/scripts/generate_docs.py
  ```

[> Tests
--------

Unit tests are in `tests/`. To run all tests:

```bash
python3 -m pytest tests/ -v
```

Run individual test files:

```bash
python3 -m pytest tests/test_usb2_packet.py -v
python3 -m pytest tests/test_usb2_transfer.py -v
```

[> Available Modules
--------------------

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
| `liteusb.gateware.usb.usb2.endpoints.stream` | Bulk/Interrupt stream endpoints |
| `liteusb.gateware.usb.usb2.endpoints.isochronous` | Isochronous memory-mapped IN |
| `liteusb.gateware.usb.usb2.endpoints.isochronous_stream_in` | Isochronous stream IN |
| `liteusb.gateware.usb.usb2.endpoints.isochronous_stream_out` | Isochronous stream OUT |
| `liteusb.gateware.usb.usb2.endpoints.status` | Signal/status IN endpoint |

### PHY Interfaces

| Module | Description |
|--------|-------------|
| `liteusb.gateware.interface.ulpi` | ULPI PHY interface and translator |
| `liteusb.gateware.interface.utmi` | UTMI interface (simulation aid) |
| `liteusb.gateware.interface.gateware_phy` | Pure gateware USB PHY |

### Devices

| Module | Description |
|--------|-------------|
| `liteusb.gateware.usb.devices.acm` | CDC-ACM USB serial device |

### Utilities

| Module | Description |
|--------|-------------|
| `liteusb.gateware.utils.cdc` | Clock domain crossing |
| `liteusb.gateware.utils.bus` | Bus utilities (multiplexers) |
| `liteusb.gateware.utils.io` | I/O utilities |
| `liteusb.gateware.stream.generator` | StreamSerializer |
| `liteusb.gateware.stream.arbiter` | Stream arbiters |
| `liteusb.gateware.memory` | Transactionalized FIFO |

[> License
----------

LiteUSB is released under the **BSD 3-Clause License**. See the LICENSE file
for details.

```
Copyright (c) 2020-2024 Great Scott Gadgets <info@greatscottgadgets.com>
Copyright (c) 2025-2026 Hans Baier <foss@hans-baier.de>

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation.
3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED.
```

[> Contact
----------

E-mail: foss [AT] hans-baier.de
Issues: https://github.com/hansfbaier/liteusb/issues
