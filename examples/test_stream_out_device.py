#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for stream_out_device.py (bulk OUT → IN loopback).

Flash stream_out_device.py (--deca), then run:

    python3 test_stream_out_device.py [num_rounds] [payload_size]

Expects the device at 1209:0001 with bulk endpoints 0x01 (OUT) and
0x81 (IN) echoing every byte back.

NOTE: the device loops raw streams — every written packet comes back
verbatim, so this test writes a random pattern, reads the same number
of bytes back, and compares.
"""

import os
import sys

import usb.core
import usb.util

VID, PID = 0x1209, 0x0001
EP_OUT, EP_IN = 0x01, 0x81
# Must match MAX_BULK_PACKET_SIZE in stream_out_device.py.
MAX_PACKET = 64


def main():
    num_rounds   = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    payload_size = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_PACKET

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

    failures = 0
    for i in range(num_rounds):
        # Random pattern, different each round.
        pattern = os.urandom(payload_size)

        try:
            written = dev.write(EP_OUT, pattern, timeout=1000)
        except usb.core.USBError as e:
            print(f"FAIL: bulk write {i} error: {e}")
            failures += 1
            continue
        if written != payload_size:
            print(f"FAIL: bulk write {i}: {written}/{payload_size} bytes written")
            failures += 1
            continue

        try:
            data = bytes(dev.read(EP_IN, payload_size, timeout=1000))
        except usb.core.USBError as e:
            print(f"FAIL: bulk read {i} error: {e}")
            failures += 1
            continue

        ok_len = len(data) == payload_size
        ok_echo = data == pattern
        status = "OK" if (ok_len and ok_echo) else "FAIL"
        print(f"round {i}: wrote {written} bytes, read {len(data)} bytes, "
              f"echo={'match' if ok_echo else 'MISMATCH'}  [{status}]")
        if not ok_echo and ok_len:
            diff = [j for j in range(len(data)) if data[j] != pattern[j]]
            print(f"  first {min(8, len(diff))} mismatched byte offsets: {diff[:8]}")
            print(f"  sent: {pattern[:16].hex()}  got: {data[:16].hex()}")
        if status == "FAIL":
            failures += 1

    usb.util.release_interface(dev, 0)

    if failures:
        sys.exit(f"FAIL: {failures}/{num_rounds} rounds failed")
    print(f"PASS: {num_rounds}/{num_rounds} loopback rounds of {payload_size} bytes OK")


if __name__ == "__main__":
    main()
