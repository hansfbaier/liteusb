#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020-2025 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""
Simple USB Device Example
=========================

This example demonstrates how to create a basic USB device using the LiteUSB
framework. It creates a simple USB device with:

- Standard USB control endpoint (EP0)
- Device descriptors for enumeration
- Activity LEDs for TX/RX visualization

The device can be integrated into a LiteX platform or used standalone with
a suitable PHY interface (ULPI or UTMI).

Usage:
    python simple_device.py

    To build for a specific platform, integrate this module into a LiteX target.
"""

import os
import sys

from migen import *

from usb_protocol.emitters import DeviceDescriptorCollection

from liteusb import USBDevice
from liteusb.gateware.interface.ulpi import ULPIInterface


class SimpleUSBDevice(Module):
    """Simple USB device example with control endpoint.
    
    This module demonstrates the minimal configuration needed to create
    a functional USB device using LiteUSB. It includes standard device
    descriptors and a control endpoint for USB enumeration.
    
    Parameters
    ----------
    phy : ULPIInterface or UTMIInterface
        The PHY interface to use for USB communication.
        Can be ULPI (for High/Full speed) or UTMI (for Full speed).
    
    Attributes
    ----------
    usb : USBDevice
        The USB device instance
    tx_activity_led : Signal
        Output signal indicating USB transmit activity
    rx_activity_led : Signal
        Output signal indicating USB receive activity
    suspended : Signal
        Output signal indicating USB suspend state
    """

    def __init__(self, phy):
        # Store PHY interface
        self.phy = phy
        
        # Activity signals for LEDs or monitoring
        self.tx_activity_led = Signal()
        self.rx_activity_led = Signal()
        self.suspended = Signal()
        
        # Create USB device with the provided PHY
        self.submodules.usb = usb = USBDevice(bus=phy)
        
        # Create and add standard control endpoint with descriptors
        descriptors = self._create_descriptors()
        usb.add_standard_control_endpoint(descriptors)
        
        # Connect device control signals
        self.comb += [
            # Connect the device (pull-up enable)
            usb.connect.eq(1),
            
            # Optionally force full-speed only (disable high-speed)
            # Set LITEUSB_FULL_SPEED=1 environment variable to enable
            usb.full_speed_only.eq(int(os.getenv('LITEUSB_FULL_SPEED', '0'))),
            
            # Export activity signals
            self.tx_activity_led.eq(usb.tx_activity_led),
            self.rx_activity_led.eq(usb.rx_activity_led),
            self.suspended.eq(usb.suspended),
        ]

    def _create_descriptors(self):
        """Create USB device descriptors.
        
        Creates the standard USB descriptors required for device enumeration:
        - Device descriptor (vendor/product IDs, strings)
        - Configuration descriptor
        - Interface descriptor
        
        Returns
        -------
        DeviceDescriptorCollection
            Collection of USB descriptors for the device
        """
        descriptors = DeviceDescriptorCollection()
        
        # Device descriptor
        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = 0x1209   # Generic vendor ID (pid.codes)
            d.idProduct          = 0x0001   # Test product
            d.bcdDevice          = 1.00    # Device version 1.0
            
            d.iManufacturer      = "LiteUSB"
            d.iProduct           = "Simple Device Example"
            d.iSerialNumber      = "0001"
            
            d.bNumConfigurations = 1
        
        # Configuration descriptor
        with descriptors.ConfigurationDescriptor() as c:
            c.bConfigurationValue = 1
            c.iConfiguration      = "Default Configuration"
            c.bmAttributes        = 0x80    # Bus powered
            c.bMaxPower           = 50      # 100mA (in 2mA units)
            
            # Interface descriptor
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber   = 0
                i.bAlternateSetting  = 0
                i.bInterfaceClass    = 0xFF  # Vendor specific
                i.bInterfaceSubclass  = 0x00
                i.bInterfaceProtocol = 0x00
                i.iInterface         = "LiteUSB Interface"
                
                # Example endpoint descriptors (optional)
                # Bulk OUT endpoint
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x01  # EP1 OUT
                    e.bmAttributes     = 0x02  # Bulk
                    e.wMaxPacketSize   = 512   # Max packet size for HS
                    e.bInterval        = 0
                
                # Bulk IN endpoint
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81  # EP1 IN
                    e.bmAttributes     = 0x02  # Bulk
                    e.wMaxPacketSize   = 512   # Max packet size for HS
                    e.bInterval        = 0
        
        return descriptors


class SimpleUSBDeviceWithPlatform(Module):
    """Simple USB device integrated with a LiteX platform.
    
    This variant shows how to integrate the USB device into a full
    LiteX platform with proper clock domain handling.
    
    Parameters
    ----------
    platform : Platform
        The LiteX platform instance (e.g., from litex_boards)
    """
    
    def __init__(self, platform):
        # Request the ULPI/UTMI PHY from the platform
        # Platform must define a 'usb' resource
        try:
            phy = platform.request("usb")
        except:
            # For simulation or platforms without USB resource,
            # create a mock PHY interface
            from liteusb.gateware.interface.utmi import UTMIInterface
            phy = UTMIInterface()
        
        # Create the USB device
        self.submodules.simple_device = SimpleUSBDevice(phy)
        
        # Connect to platform LEDs if available
        if hasattr(platform, 'request'):
            try:
                led0 = platform.request("user_led", 0)
                led1 = platform.request("user_led", 1)
                led2 = platform.request("user_led", 2)
                
                self.comb += [
                    led0.eq(self.simple_device.tx_activity_led),
                    led1.eq(self.simple_device.rx_activity_led),
                    led2.eq(self.simple_device.suspended),
                ]
            except:
                pass  # LEDs are optional


def main():
    """Main entry point for standalone usage.
    
    When run directly, this creates a simulation or generates Verilog
    for the simple USB device.
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description="LiteUSB Simple Device Example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate Verilog output
    python simple_device.py --build
    
    # Run simulation (requires testbench)
    python simple_device.py --simulate
    
    # Force full-speed mode
    LITEUSB_FULL_SPEED=1 python simple_device.py --build
"""
    )
    
    parser.add_argument(
        '--build', 
        action='store_true',
        help='Generate Verilog output'
    )
    parser.add_argument(
        '--simulate',
        action='store_true',
        help='Run simulation'
    )
    parser.add_argument(
        '--output',
        default='simple_device.v',
        help='Output filename (default: simple_device.v)'
    )
    
    args = parser.parse_args()
    
    # Create a mock PHY for standalone operation
    from liteusb.gateware.interface.utmi import UTMIInterface
    from migen.fhdl.verilog import convert
    
    # Create the USB device module
    dut = SimpleUSBDevice(UTMIInterface())
    
    if args.build:
        # Generate Verilog
        print(f"Generating Verilog output to {args.output}...")
        ios = {
            dut.tx_activity_led,
            dut.rx_activity_led,
            dut.suspended,
        }
        convert(dut, ios, name="simple_usb_device").write(args.output)
        print(f"Done! Output written to {args.output}")
        
    elif args.simulate:
        print("Simulation mode - would run testbench here")
        print("(Simulation requires a testbench implementation)")
        
    else:
        parser.print_help()
        print("\nNote: This example is designed to be integrated into a LiteX platform.")
        print("For standalone use, use --build to generate Verilog.")


if __name__ == "__main__":
    main()
