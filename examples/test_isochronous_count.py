#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for isochronous_count.py (isochronous-IN counter).

Flash isochronous_count.py (--deca), then run:

    python3 test_isochronous_count.py [num_reads]

Expects the device at 1209:0005 with an isochronous-IN endpoint 0x81
streaming address-valued bytes (0,1,2,... mod 256).

Supports both High Speed (3x1024 bytes/microframe) and Full Speed
(1x1023 bytes/frame).  pyusb has no isochronous API, so this uses the
libusb1 (usb1) wrapper with async transfers.
"""

import sys

import usb1

VID, PID, EP_IN = 0x1209, 0x0005, 0x81

# libusb speed constants
LIBUSB_SPEED_HIGH = 3


def monotonic_run(data):
    """Count monotonically-incrementing (mod 256) byte pairs in data."""
    good = sum(1 for i in range(len(data) - 1)
               if (data[i + 1] - data[i]) % 256 == 1)
    return good, max(len(data) - 1, 0)


def iso_read(context, handle, packet_size, num_packets):
    """Perform one isochronous IN read; returns the received bytes."""
    received = []

    def callback(transfer):
        if transfer.getStatus() == usb1.TRANSFER_COMPLETED:
            for _setup, buf in transfer.iterISO():
                received.append(bytes(buf))

    transfer = handle.getTransfer(num_packets)
    transfer.setIsochronous(EP_IN, packet_size * num_packets, callback, timeout=1000)
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
        speed = device.getDeviceSpeed()
        print(f"device on bus {device.getBusNumber()} address "
              f"{device.getDeviceAddress()}, speed {speed}")

        if speed >= LIBUSB_SPEED_HIGH:
            packet_size  = 1024
            num_packets  = 3
        else:
            packet_size  = 1023
            num_packets  = 1

        total = good_total = 0
        for i in range(num_reads):
            try:
                data = iso_read(context, handle, packet_size, num_packets)
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
