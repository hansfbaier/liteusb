#!/usr/bin/env python3
"""
USB packet utilities for testing.

This module provides functions for creating USB packets as lists of bytes.
"""


class CrcAlgorithm:
    """Represents the parameters of a CRC algorithm."""

    def __init__(self, width, polynomial, seed=0, lsb_first=True, xor_mask=0):
        self.width = width
        self.polynomial = polynomial
        self.seed = seed
        self.lsb_first = lsb_first
        self.xor_mask = xor_mask


class CrcRegister:
    """Holds the intermediate state of the CRC algorithm."""

    def __init__(self, crc_algorithm):
        self.crc_algorithm = crc_algorithm
        self.bit_mask = (1 << crc_algorithm.width) - 1
        self.poly_mask = crc_algorithm.polynomial & self.bit_mask
        if crc_algorithm.lsb_first:
            self.poly_mask = self._reflect(self.poly_mask, crc_algorithm.width)
        self.reset()

    def _reflect(self, value, width):
        return sum(((value >> x) & 1) << (width - 1 - x) for x in range(width))

    def reset(self):
        self.value = int(self.crc_algorithm.seed)

    def take_bit(self, bit):
        if self.crc_algorithm.lsb_first:
            out_bit = self.value & 1
            self.value >>= 1
            if out_bit ^ bool(bit):
                self.value ^= self.poly_mask
        else:
            out_bit = (self.value >> (self.crc_algorithm.width - 1)) & 1
            self.value <<= 1
            self.value &= self.bit_mask
            if out_bit ^ bool(bit):
                self.value ^= self.poly_mask

    def take_word(self, word, width):
        if self.crc_algorithm.lsb_first:
            bit_list = range(0, width)
        else:
            bit_list = range(width - 1, -1, -1)
        for n in bit_list:
            self.take_bit((word >> n) & 1)

    def get_final_value(self):
        return self.value ^ self.crc_algorithm.xor_mask


# CRC-5-USB: width=5 poly=0x05 init=0x1f refin=true refout=true xorout=0x1f
CRC5_USB = CrcAlgorithm(
    width=5,
    polynomial=0x05,
    seed=0x1f,
    lsb_first=True,
    xor_mask=0x1f
)

# CRC-16-USB: width=16 poly=0x8005 init=0xffff refin=true refout=true xorout=0xffff
CRC16_USB = CrcAlgorithm(
    width=16,
    polynomial=0x8005,
    seed=0xffff,
    lsb_first=True,
    xor_mask=0xffff
)


def reflect_byte(value):
    """Reverse the bits in a byte (LSB first to MSB first)."""
    result = 0
    for i in range(8):
        if (value >> i) & 1:
            result |= 1 << (7 - i)
    return result


# USB PID values
class PID:
    """USB Packet IDs."""
    OUT = 0x01
    IN = 0x09
    SOF = 0x05
    SETUP = 0x0D
    DATA0 = 0x03
    DATA1 = 0x0B
    DATA2 = 0x07
    MDATA = 0x0F
    ACK = 0x02
    NAK = 0x0A
    STALL = 0x0E
    NYET = 0x06
    PRE = 0x0C
    ERR = 0x0C
    SPLIT = 0x08
    PING = 0x04


def pid_byte(pid):
    """Return PID as a byte with its complement in upper nibble."""
    inverted_pid = pid ^ 0x0F
    return (inverted_pid << 4) | pid


def crc5_token(addr, ep):
    """Compute CRC5 for a token packet.

    Args:
        addr: 7-bit device address
        ep: 4-bit endpoint number

    Returns:
        5-bit CRC value

    >>> hex(crc5_token(0, 0))
    '0x2'
    >>> hex(crc5_token(92, 0))
    '0x1c'
    >>> hex(crc5_token(3, 0))
    '0xa'
    >>> hex(crc5_token(56, 4))
    '0xb'
    """
    reg = CrcRegister(CRC5_USB)
    reg.take_word(addr, 7)
    reg.take_word(ep, 4)
    return reg.get_final_value() & 0x1f


def crc5_sof(frame):
    """Compute CRC5 for a SOF packet.

    Args:
        frame: 11-bit frame number

    Returns:
        5-bit CRC value

    >>> hex(crc5_sof(1429))
    '0x10'
    >>> hex(crc5_sof(1013))
    '0x14'
    """
    reg = CrcRegister(CRC5_USB)
    reg.take_word(frame, 11)
    crc_val = reg.get_final_value()
    # Reverse the bits of the result
    return int(bin(crc_val | 0x10000000)[::-1][:5], 2)


def crc5(token_data):
    """Compute USB CRC5 for token data.

    Args:
        token_data: An integer containing the token data bits

    Returns:
        5-bit CRC value
    """
    reg = CrcRegister(CRC5_USB)
    # Count bits needed
    if token_data == 0:
        bits = 1
    else:
        bits = token_data.bit_length()
    reg.take_word(token_data, bits)
    return reg.get_final_value() & 0x1f


def crc16(data):
    """Compute USB CRC16 for data.

    USB CRC16 parameters:
    - width=16
    - poly=0x8005
    - init=0xffff
    - refin=true
    - refout=true
    - xorout=0xffff

    Args:
        data: List of bytes to compute CRC over

    Returns:
        List of two bytes [CRC_L, CRC_H] in little-endian order

    >>> [hex(b) for b in crc16([0])]
    ['0x40', '0xbf']
    >>> [hex(b) for b in crc16([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00])]
    ['0xeb', '0x94']
    """
    reg = CrcRegister(CRC16_USB)
    for d in data:
        reg.take_word(d, 8)
    crc_val = reg.get_final_value()
    return [crc_val & 0xff, (crc_val >> 8) & 0xff]


def token_packet(pid, address, endpoint):
    """Create a USB token packet.

    Token packet format (24 bits total, LSB first):
    - PID (8 bits)
    - Address (7 bits)
    - Endpoint (4 bits)
    - CRC5 (5 bits)

    Args:
        pid: Packet ID (use PID.OUT, PID.IN, PID.SETUP)
        address: 7-bit device address (0-127)
        endpoint: 4-bit endpoint number (0-15)

    Returns:
        List of 3 bytes representing the token packet

    >>> [hex(b) for b in token_packet(PID.SETUP, 0, 0)]
    ['0x2d', '0x0', '0x10']
    >>> [hex(b) for b in token_packet(PID.IN, 3, 0)]
    ['0x69', '0x3', '0x50']
    >>> [hex(b) for b in token_packet(PID.OUT, 0x3a, 0xa)]
    ['0xe1', '0x3a', '0x3d']
    """
    assert address < 128, f"Address must be 7-bit: {address}"
    assert endpoint < 16, f"Endpoint must be 4-bit: {endpoint}"

    # PID byte with complement
    pid_b = pid_byte(pid)

    # Compute CRC5 over address and endpoint
    crc = crc5_token(address, endpoint)

    # Build the 16-bit token data LSB first:
    # Bits [6:0] = address
    # Bits [10:7] = endpoint
    # Bits [15:11] = CRC5
    # But we need to store LSB first, so:
    # Byte 0: addr[6:0] + ep[0] in bit 7
    # Byte 1: ep[3:1] + crc[4:0] in bits [7:3]

    byte0 = (address & 0x7f) | ((endpoint & 0x01) << 7)
    byte1 = ((endpoint >> 1) & 0x07) | ((crc & 0x1f) << 3)

    return [pid_b, byte0, byte1]


def data_packet(pid, data):
    """Create a USB data packet with CRC16.

    Data packet format:
    - PID (8 bits)
    - Data (0-1024 bytes)
    - CRC16 (16 bits)

    Args:
        pid: Packet ID (use PID.DATA0 or PID.DATA1)
        data: List of data bytes

    Returns:
        List of bytes: [PID, data..., CRC_L, CRC_H]

    >>> [hex(b) for b in data_packet(PID.DATA0, [])]
    ['0xc3', '0x0', '0x0']
    >>> [hex(b) for b in data_packet(PID.DATA1, [0x80, 0x06])]
    ['0x4b', '0x80', '0x6', '0x1f', '0x8d']
    """
    data = list(data)
    crc = crc16(data)

    return [pid_byte(pid)] + data + crc


def handshake_packet(pid):
    """Create a USB handshake packet.

    Handshake packet format:
    - PID (8 bits) only

    Args:
        pid: Packet ID (use PID.ACK, PID.NAK, or PID.STALL)

    Returns:
        List with single PID byte

    >>> [hex(b) for b in handshake_packet(PID.ACK)]
    ['0xd2']
    >>> [hex(b) for b in handshake_packet(PID.NAK)]
    ['0x5a']
    """
    return [pid_byte(pid)]


def sof_packet(frame):
    """Create a USB SOF (Start of Frame) packet.

    SOF packet format:
    - PID (8 bits)
    - Frame number (11 bits)
    - CRC5 (5 bits)

    Args:
        frame: 11-bit frame number (0-2047)

    Returns:
        List of 3 bytes representing the SOF packet

    >>> [hex(b) for b in sof_packet(0)]
    ['0xa5', '0x0', '0x40']
    >>> [hex(b) for b in sof_packet(1429)]
    ['0xa5', '0x95', '0x85']
    """
    assert frame < 2048, f"Frame number must be 11-bit: {frame}"

    pid_b = pid_byte(PID.SOF)
    crc = crc5_sof(frame)

    # Frame number is 11 bits, CRC is 5 bits, total 16 bits
    # Byte 0: frame[7:0]
    # Byte 1: frame[10:8] in bits [2:0], crc[4:0] in bits [7:3]
    byte0 = frame & 0xff
    byte1 = ((frame >> 8) & 0x07) | ((crc & 0x1f) << 3)

    return [pid_b, byte0, byte1]


if __name__ == "__main__":
    import doctest
    doctest.testmod()
