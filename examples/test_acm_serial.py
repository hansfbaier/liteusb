#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for acm_serial.py (CDC-ACM serial loopback).

Flash acm_serial.py (--deca), then run:

    python3 test_acm_serial.py [port] [num_rounds] [payload_size]

The example loops the ACM data stream back to the host, so every byte
written to the tty must come back verbatim.

    python3 test_acm_serial.py /dev/ttyACM0 8 64
"""

import os
import sys
import time

import serial

DEFAULT_PORT = "/dev/ttyACM0"


def main():
    port         = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT
    num_rounds   = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    payload_size = int(sys.argv[3]) if len(sys.argv) > 3 else 64

    try:
        ser = serial.Serial(port, baudrate=115200, timeout=2)
    except serial.SerialException as e:
        sys.exit(f"FAIL: cannot open {port}: {e}")

    print(f"port: {port} open (loopback echo expected)")

    failures = 0
    for i in range(num_rounds):
        pattern = os.urandom(payload_size)
        ser.reset_input_buffer()
        ser.write(pattern)
        ser.flush()

        got = b""
        deadline = time.monotonic() + 2.0
        while len(got) < payload_size and time.monotonic() < deadline:
            got += ser.read(payload_size - len(got))

        ok = got == pattern
        status = "OK" if ok else "FAIL"
        print(f"round {i}: wrote {payload_size}, read {len(got)}, "
              f"echo={'match' if ok else 'MISMATCH'}  [{status}]")
        if not ok and got:
            diffs = [j for j in range(min(len(got), payload_size))
                     if got[j] != pattern[j]]
            print(f"  first {min(8, len(diffs))} mismatched offsets: {diffs[:8]}")
            print(f"  sent: {pattern[:16].hex()}  got: {got[:16].hex()}")
        failures += not ok

    ser.close()

    if failures:
        sys.exit(f"FAIL: {failures}/{num_rounds} rounds failed")
    print(f"PASS: {num_rounds}/{num_rounds} loopback rounds of {payload_size} bytes OK")


if __name__ == "__main__":
    main()
