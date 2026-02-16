#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from migen import *
from migen.genlib.record import Record
from migen.sim import run_simulation
from liteusb.gateware.usb.usb2.packet import USBTokenDetector

# Create UTMI interface like the test does
utmi = Record([
    ("rx_data",   8),
    ("rx_active", 1),
    ("rx_valid",  1)
])

# Create the DUT
dut = USBTokenDetector(utmi=utmi)

# Print the interface layout
print("DUT interface fields:")
for name, width in dut.interface.layout:
    print(f"  {name}: {width}")

# Check token detection signals
print("\nToken detection signals (combinatorial):")
print(f"  TOKEN_SUFFIX = {dut.TOKEN_SUFFIX}")
print(f"  SOF_PID = {dut.SOF_PID}")

def testbench():
    # Initial state
    print(f"Initial rx_active: {(yield utmi.rx_active)}")
    print(f"Initial rx_valid: {(yield utmi.rx_valid)}")
    print(f"Initial rx_data: {(yield utmi.rx_data)}")
    print(f"Initial fsm state: {(yield dut.fsm.state)}")
    
    # Start packet
    yield utmi.rx_active.eq(1)
    yield utmi.rx_valid.eq(1)
    yield
    
    print(f"\nAfter start - rx_active: {(yield utmi.rx_active)}")
    print(f"After start - fsm state: {(yield dut.fsm.state)}")
    
    # Provide first byte (PID for OUT token)
    yield utmi.rx_data.eq(0b11100001)  # 0xE1
    yield
    
    print(f"\nAfter PID - rx_data: {(yield utmi.rx_data):08b}")
    print(f"After PID - fsm state: {(yield dut.fsm.state)}")
    
    # Provide second byte
    yield utmi.rx_data.eq(0b00111010)  # 0x3A
    yield
    
    print(f"\nAfter byte 2 - fsm state: {(yield dut.fsm.state)}")
    
    # Provide third byte
    yield utmi.rx_data.eq(0b00111101)  # 0x3D
    yield
    
    print(f"\nAfter byte 3 - fsm state: {(yield dut.fsm.state)}")
    
    # End packet
    yield utmi.rx_active.eq(0)
    yield utmi.rx_valid.eq(0)
    yield
    
    print(f"\nAfter end - fsm state: {(yield dut.fsm.state)}")
    print(f"After end - new_token: {(yield dut.interface.new_token)}")
    yield
    print(f"After end + 1 - new_token: {(yield dut.interface.new_token)}")
    yield
    print(f"After end + 2 - new_token: {(yield dut.interface.new_token)}")

# Run simulation
run_simulation(dut, testbench(), clocks={'sys': 1e-6})
