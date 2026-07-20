#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" USB packet utilities for testing. """


def _pid_byte(pid):
    """Return the full PID byte including the check bits.
    
    USB PIDs are 4 bits with 4 check bits (inverted PID) appended.
    """
    inverted_pid = pid ^ 0b1111
    full_pid = (inverted_pid << 4) | pid
    return full_pid


def encode_data(data):
    """Converts array of 8-bit ints into string of 0s and 1s (LSB first)."""
    output = ""
    for b in data:
        output += (f"{int(b):08b}")[::-1]
    return output


def encode_pid(value):
    """Encode a PID into a bit string."""
    if isinstance(value, int):
        pid_value = value
    else:
        pid_value = int(value)
    return encode_data([_pid_byte(pid_value)])


# CRC-5/USB polynomial: 0x05, init=0x1f, refin=true, refout=true, xorout=0x1f
def crc5_token(addr, ep):
    """Calculate CRC5 for a token packet.

    CRC5 covers the address (7 bits) and endpoint (4 bits).

    >>> hex(crc5_token(0, 0))
    '0x2'
    >>> hex(crc5_token(92, 0))
    '0x1c'
    >>> hex(crc5_token(3, 0))
    '0xa'
    >>> hex(crc5_token(56, 4))
    '0xb'
    """
    crc = 0x1f

    # Process address (7 bits, LSB first)
    for i in range(7):
        bit = (addr >> i) & 1
        crc = _crc5_bit(crc, bit)

    # Process endpoint (4 bits, LSB first)
    for i in range(4):
        bit = (ep >> i) & 1
        crc = _crc5_bit(crc, bit)

    # NOTE: no output reflection here — the reflected bit-serial
    # implementation above already embodies refin=refout=true.
    return crc ^ 0x1f


def _crc5_bit(crc, bit):
    """Process one bit for CRC5 calculation."""
    crc_bit = crc & 1
    if crc_bit != bit:
        crc = (crc >> 1) ^ 0x14  # 0x14 = 10100 (polynomial)
    else:
        crc = crc >> 1
    return crc


def _reflect5(val):
    """Reflect 5 bits."""
    result = 0
    for i in range(5):
        if val & (1 << i):
            result |= 1 << (4 - i)
    return result


def crc16(data):
    """Calculate CRC16 for data.

    CRC-16/USB polynomial: 0x8005 (reflected: 0xA001),
    init=0xffff, refin=true, refout=true, xorout=0xffff.
    Returns [low_byte, high_byte] (appended low byte first per USB spec).

    >>> [hex(b) for b in crc16(b"123456789")]
    ['0xc8', '0xb4']
    """
    crc = 0xffff

    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc = crc >> 1

    crc ^= 0xffff

    # Return low byte first (USB convention)
    return [crc & 0xff, (crc >> 8) & 0xff]


def token_packet(pid, addr, endp):
    """Create a token packet bit string.
    
    Format: PID (8 bits) + Address (7 bits) + Endpoint (4 bits) + CRC5 (5 bits)
    
    Args:
        pid: The packet ID (e.g., PID.OUT, PID.IN, PID.SETUP)
        addr: The device address (7 bits)
        endp: The endpoint number (4 bits)
    
    Returns:
        Bit string with LSB first encoding.
    """
    assert addr < 128, f"Address {addr} out of range"
    assert endp < 16, f"Endpoint {endp} out of range"
    
    packet = encode_pid(pid)
    packet += "{0:07b}".format(addr)[::-1]  # 7 bits address, LSB first
    packet += "{0:04b}".format(endp)[::-1]  # 4 bits endpoint, LSB first
    packet += "{0:05b}".format(crc5_token(addr, endp))[::-1]  # 5 bits CRC5, LSB first
    
    assert len(packet) == 24, f"Token packet length should be 24, got {len(packet)}"
    return packet


def data_packet(pid, payload):
    """Create a data packet bit string.
    
    Format: PID (8 bits) + Data (N bytes) + CRC16 (2 bytes)
    
    Args:
        pid: The packet ID (e.g., PID.DATA0, PID.DATA1)
        payload: List of bytes to send
    
    Returns:
        Bit string with LSB first encoding.
    """
    payload = list(payload)
    return encode_pid(pid) + encode_data(payload + crc16(payload))


def handshake_packet(pid):
    """Create a handshake packet bit string.
    
    Format: PID (8 bits only)
    
    Args:
        pid: The packet ID (e.g., PID.ACK, PID.NAK, PID.STALL)
    
    Returns:
        Bit string with LSB first encoding.
    """
    return encode_pid(pid)
