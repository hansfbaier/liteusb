#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Host-side test for simple_device.py (enumeration-only device).

Flash simple_device.py (--deca), then run:

    python3 test_simple_device.py

The device only implements the EP0 control endpoint, so this test
verifies enumeration: device/config descriptor reads, string
descriptors, and SET_CONFIGURATION.
"""

import sys

import usb.core
import usb.util

VID, PID = 0x1209, 0x0001

EXPECTED = {
    "idVendor":          VID,
    "idProduct":         PID,
    "bNumConfigurations": 1,
    "manufacturer":      "LiteUSB",
    "product":           "Simple Device Example",
    "serial":            "0001",
}


def check(name, got, want):
    ok = got == want
    print(f"  {name:20s} = {got!r:30s} (expect {want!r})  {'OK' if ok else 'FAIL'}")
    return ok


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("FAIL: device %04x:%04x not found (flashed? replugged?)" % (VID, PID))

    print(f"device on bus {dev.bus} address {dev.address}, speed {dev.speed}")

    failures = 0

    print("device descriptor:")
    failures += not check("idVendor", dev.idVendor, EXPECTED["idVendor"])
    failures += not check("idProduct", dev.idProduct, EXPECTED["idProduct"])
    failures += not check("bNumConfigurations", dev.bNumConfigurations,
                          EXPECTED["bNumConfigurations"])

    print("string descriptors:")
    try:
        failures += not check("iManufacturer", dev.manufacturer, EXPECTED["manufacturer"])
        failures += not check("iProduct", dev.product, EXPECTED["product"])
        failures += not check("iSerialNumber", dev.serial_number, EXPECTED["serial"])
    except usb.core.USBError as e:
        print(f"  FAIL: string descriptor read error: {e}")
        failures += 1

    print("configuration descriptor:")
    try:
        cfg = dev.get_active_configuration()
        interfaces = list(cfg.interfaces())
        failures += not check("num interfaces", len(interfaces), 1)
        eps = list(interfaces[0].endpoints())
        addrs = sorted(e.bEndpointAddress for e in eps)
        failures += not check("endpoints", addrs, [0x01, 0x81])
    except usb.core.USBError as e:
        print(f"  FAIL: configuration read error: {e}")
        failures += 1

    # Exercise a few raw control transfers (descriptor re-reads via EP0).
    print("raw EP0 GET_DESCRIPTOR (x8):")
    for i in range(8):
        try:
            d = dev.ctrl_transfer(0x80, 6, 0x0100, 0, 18, timeout=1000)
            # idVendor = bytes 8-9 of the device descriptor (little-endian).
            ok = len(d) == 18 and (d[8] | (d[9] << 8)) == VID
            if not ok:
                failures += 1
                print(f"  read {i}: bad descriptor: {bytes(d).hex()}")
        except usb.core.USBError as e:
            failures += 1
            print(f"  read {i}: FAIL: {e}")
    else:
        print("  all descriptor re-reads OK")

    if failures:
        sys.exit(f"FAIL: {failures} checks failed")
    print("PASS: device enumerates correctly, EP0 control transfers work")


if __name__ == "__main__":
    main()
