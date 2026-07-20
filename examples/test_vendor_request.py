#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for vendor_request.py (vendor request LED control).

Flash vendor_request.py (--deca), then run:

    python3 test_vendor_request.py

Device: 1209:0007, vendor request 0 (SET_LEDS) takes a 1-byte data stage
and drives it onto the board LEDs (DECA: LED4..7 show the lower nibble,
active-low).

Positive checks: request 0 with a payload must be ACKed.
Negative check:  an unknown vendor request must STALL.
Watch the board LEDs to see the values appear.
"""

import sys
import time

import usb.core
import usb.util

VID, PID      = 0x1209, 0x0007
REQUEST_LEDS  = 0
REQ_TYPE_OUT  = 0x40   # host-to-device | vendor | device


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("FAIL: device %04x:%04x not found (flashed? replugged?)" % (VID, PID))

    try:
        product = dev.product
    except Exception:
        product = "(string read failed)"
    print(f"device: {product} on bus {dev.bus} address {dev.address}, speed {dev.speed}")

    failures = 0

    # Positive: SET_LEDS with various patterns must succeed.
    patterns = [0x00, 0xFF, 0xA5, 0x5A, 0x01, 0x02, 0x04, 0x08, 0x0F]
    for v in patterns:
        try:
            written = dev.ctrl_transfer(REQ_TYPE_OUT, REQUEST_LEDS, 0, 0,
                                        bytes([v]), timeout=1000)
            ok = written == 1
        except usb.core.USBError as e:
            print(f"SET_LEDS {v:#04x}: FAIL: {e}")
            failures += 1
            continue
        print(f"SET_LEDS {v:#04x}: {'OK' if ok else 'FAIL'} "
              f"(LED7..4 nibble = {v & 0x0F:#x}, active-low)")
        failures += not ok
        time.sleep(0.3)  # leave time to see the LEDs

    # Negative: unknown vendor request must STALL.
    try:
        dev.ctrl_transfer(REQ_TYPE_OUT, 0x42, 0, 0, bytes([0x00]), timeout=1000)
        print("unknown request 0x42: FAIL (unexpectedly ACKed)")
        failures += 1
    except usb.core.USBError as e:
        # libusb reports a stall as PIPE error
        print(f"unknown request 0x42: OK (stalled as expected: {e})")

    # Sanity: standard GET_DESCRIPTOR still works afterwards.
    try:
        d = dev.ctrl_transfer(0x80, 6, 0x0100, 0, 18, timeout=1000)
        ok = len(d) == 18 and (d[8] | (d[9] << 8)) == VID
        print(f"GET_DESCRIPTOR after vendor reqs: {'OK' if ok else 'FAIL'}")
        failures += not ok
    except usb.core.USBError as e:
        print(f"GET_DESCRIPTOR after vendor reqs: FAIL: {e}")
        failures += 1

    if failures:
        sys.exit(f"FAIL: {failures} checks failed")
    print("PASS: vendor requests ACKed, unknown request stalled, EP0 healthy")


if __name__ == "__main__":
    main()
