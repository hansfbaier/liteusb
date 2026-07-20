#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for isochronous_count.py (isochronous-IN counter).

Flash isochronous_count.py (--deca), then run:

    python3 test_isochronous_count.py [num_reads]

Expects the device at 1209:0001 with an isochronous-IN endpoint 0x81
(up to 3x1024 bytes per microframe) streaming address-valued bytes
(0,1,2,... mod 256).

NOTE: isochronous 3x1024 requires HIGH SPEED. At Full Speed the host
will (correctly) refuse the endpoint. pyusb has no isochronous API,
so this uses the libusb1 (usb1) wrapper with async transfers.
"""

import sys

import usb1

VID, PID, EP_IN = 0x1209, 0x0001, 0x81
PACKET_SIZE      = 1024
PACKETS_PER_READ = 3
READ_SIZE        = PACKET_SIZE * PACKETS_PER_READ


def monotonic_run(data):
    """Count monotonically-incrementing (mod 256) byte pairs in data."""
    good = sum(1 for i in range(len(data) - 1)
               if (data[i + 1] - data[i]) % 256 == 1)
    return good, max(len(data) - 1, 0)


def iso_read(context, handle, num_packets=PACKETS_PER_READ):
    """Perform one isochronous IN read; returns the received bytes."""
    received = []

    def callback(transfer):
        if transfer.getStatus() == usb1.TRANSFER_COMPLETED:
            for _setup, buf in transfer.iterISO():
                received.append(bytes(buf))

    transfer = handle.getTransfer(num_packets)
    transfer.setIsochronous(EP_IN, PACKET_SIZE * num_packets, callback, timeout=1000)
    transfer.submit()
    context.handleEventsTimeout(tv=2)
    return b"".join(received)


def main():
    num_reads = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    with usb1.USBContext() as context:
        handle = context.openByVendorIDAndProductID(VID, PID)
        if handle is None:
            sys.exit("FAIL: device %04x:%04x not found (flashed? replugged?)" % (VID, PID))

        try:
            handle.setAutoDetachKernelDriver(True)
        except usb1.USBError:
            pass
        handle.claimInterface(0)

        device = handle.getDevice()
        print(f"device on bus {device.getBusNumber()} address "
              f"{device.getDeviceAddress()}, speed {device.getDeviceSpeed()}")

        total = good_total = 0
        for i in range(num_reads):
            try:
                data = iso_read(context, handle)
            except usb1.USBError as e:
                handle.releaseInterface(0)
                sys.exit(f"FAIL: isochronous read {i} error: {e}")

            good, pairs = monotonic_run(data)
            total += pairs
            good_total += good
            holes = pairs - good
            print(f"read {i}: {len(data)} bytes, monotonic pairs {good}/{pairs}"
                  + (f"  ({holes} holes — isoc packet loss)" if holes else ""))

        handle.releaseInterface(0)

    if total == 0:
        sys.exit("FAIL: no isochronous data received")
    print(f"PASS ({good_total}/{total} monotonic pairs)")


if __name__ == "__main__":
    main()
