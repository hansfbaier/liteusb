#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for interrupt_device.py (interrupt-IN counter report).

Flash interrupt_device.py (--deca), then run:

    python3 test_interrupt_device.py [num_polls]

Expects the device at 1209:0001 with an interrupt-IN endpoint 0x81 that
reports a free-running 32-bit counter (big endian) on every poll.
"""

import sys
import time

import usb.core
import usb.util

VID, PID, EP_IN = 0x1209, 0x0001, 0x81


def main():
    num_polls = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("FAIL: device %04x:%04x not found (flashed? replugged?)" % (VID, PID))

    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    usb.util.claim_interface(dev, 0)

    try:
        product = dev.product
    except Exception:
        product = "(string read failed)"
    print(f"device: {product} on bus {dev.bus} address {dev.address}, speed {dev.speed}")

    prev = None
    for i in range(num_polls):
        try:
            data = dev.read(EP_IN, 4, timeout=1000)
        except usb.core.USBError as e:
            usb.util.release_interface(dev, 0)
            sys.exit(f"FAIL: interrupt read {i} error: {e}")

        if len(data) != 4:
            usb.util.release_interface(dev, 0)
            sys.exit(f"FAIL: expected 4 bytes, got {len(data)}")

        value = int.from_bytes(data, byteorder="big")
        ok_inc = prev is None or value != prev
        print(f"poll {i}: counter = {value} (0x{value:08x})  [{'OK' if ok_inc else 'STUCK'}]")
        if not ok_inc:
            usb.util.release_interface(dev, 0)
            sys.exit("FAIL: counter did not change between polls")
        prev = value
        time.sleep(0.1)

    usb.util.release_interface(dev, 0)
    print("PASS")


if __name__ == "__main__":
    main()
