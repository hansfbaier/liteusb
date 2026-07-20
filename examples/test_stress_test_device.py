#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for stress_test_device.py (bulk-IN constant streamer).

Flash stress_test_device.py (--deca), then run:

    python3 test_stress_test_device.py [total_mib] [chunk_bytes]

Expects the device at 1209:0001 with a bulk-IN endpoint 0x81 streaming
a constant byte (0x00) at maximum rate. Verifies the constant and
reports throughput.
"""

import sys
import time

import usb.core
import usb.util

VID, PID, EP_IN = 0x1209, 0x0001, 0x81
CONSTANT = 0x00


def main():
    total_mib   = float(sys.argv[1]) if len(sys.argv) > 1 else 8
    chunk_bytes = int(sys.argv[2])   if len(sys.argv) > 2 else 4096

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
    print(f"reading {total_mib:.1f} MiB in {chunk_bytes}-byte chunks...")

    total_target = int(total_mib * 1024 * 1024)
    total_read = 0
    bad_bytes = 0
    t0 = time.monotonic()

    while total_read < total_target:
        try:
            data = dev.read(EP_IN, chunk_bytes, timeout=2000)
        except usb.core.USBError as e:
            usb.util.release_interface(dev, 0)
            sys.exit(f"FAIL: bulk read error after {total_read} bytes: {e}")

        # Verify the constant stream.
        if any(b != CONSTANT for b in data):
            bad = sum(1 for b in data if b != CONSTANT)
            bad_bytes += bad
            print(f"  FAIL: {bad}/{len(data)} non-{CONSTANT:#04x} bytes "
                  f"at offset {total_read}")

        total_read += len(data)

    dt = time.monotonic() - t0
    mib_s = total_read / (1024 * 1024) / dt

    usb.util.release_interface(dev, 0)

    if bad_bytes:
        sys.exit(f"FAIL: {bad_bytes} wrong bytes in {total_read}")
    print(f"PASS: {total_read} bytes, all {CONSTANT:#04x}, "
          f"{dt:.1f}s -> {mib_s:.2f} MiB/s")


if __name__ == "__main__":
    main()
