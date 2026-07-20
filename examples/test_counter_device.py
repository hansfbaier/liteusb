#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for counter_device.py (bulk-IN monotonic counter).

Flash counter_device.py (--deca) or terasic_deca_counter.py, then run:

    python3 test_counter_device.py [num_reads]

Expects the device at 1209:0002 with a bulk-IN endpoint 0x81 streaming
a monotonically incrementing 8-bit counter.
"""

import sys
import time

import usb.core
import usb.util

VID, PID, EP_IN = 0x1209, 0x0002, 0x81


def main():
    num_reads = int(sys.argv[1]) if len(sys.argv) > 1 else 4

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
    for i in range(num_reads):
        try:
            data = dev.read(EP_IN, 512, timeout=1000)
        except usb.core.USBError as e:
            usb.util.release_interface(dev, 0)
            sys.exit(f"FAIL: bulk read {i} error: {e}")

        ok_len  = len(data) > 0
        ok_mono = all((data[j + 1] - data[j]) % 256 == 1 for j in range(len(data) - 1))
        ok_cont = prev is None or data[0] == prev
        prev    = (data[-1] + 1) % 256

        status = "OK" if (ok_len and ok_mono and ok_cont) else "FAIL"
        print(f"read {i}: {len(data)} bytes, first={data[0]}, last={data[-1]}, "
              f"monotonic={ok_mono}, continuous={ok_cont}  [{status}]")
        if status == "FAIL":
            usb.util.release_interface(dev, 0)
            sys.exit(1)

    usb.util.release_interface(dev, 0)
    print("PASS")


if __name__ == "__main__":
    main()
