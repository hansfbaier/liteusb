#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Gateware for creating USB2 devices. """

#
# USB Speed Definitions
# Matches UTMI xcvr_select constants
#

class USBSpeed:
    """ Class representing USB speeds. Matches UTMI xcvr_select constants. """
    HIGH = 0b00
    FULL = 0b01
    LOW  = 0b10


#
# USB PID Category Definitions
#

class USBPIDCategory:
    """ Class specifying the categories of USB PIDs. """
    MASK      = 0b0011
    
    TOKEN     = 0b0001
    DATA      = 0b0011
    HANDSHAKE = 0b0010
    SPECIAL   = 0b0000


#
# USB Direction Definitions
#

class USBDirection:
    """ Class specifying USB directions. """
    OUT = 0
    IN  = 1


#
# USB Packet ID Definitions
#

class USBPacketID(int):
    """ Class specifying all of the valid USB PIDs we can handle.

    The members are ``int`` subclasses, so both the member-level API used by LUNA
    (``USBPacketID.DATA1.byte()``, ``pid.is_data()``, ``pid.summarize()``, ``pid.name``) and the
    class-level API (``USBPacketID.byte(pid)``) are available.
    """

    # Token group (lsbs = 0b01).
    OUT   = 0b0001
    IN    = 0b1001
    SOF   = 0b0101
    SETUP = 0b1101
    
    # Data group (lsbs = 0b11).
    DATA0 = 0b0011
    DATA1 = 0b1011
    DATA2 = 0b0111
    MDATA = 0b1111
    
    # Handshake group (lsbs = 0b10)
    ACK   = 0b0010
    NAK   = 0b1010
    STALL = 0b1110
    NYET  = 0b0110
    
    # Special group.
    PRE   = 0b1100
    ERR   = 0b1100
    SPLIT = 0b1000
    PING  = 0b0100
    
    # Flag representing that the PID seems invalid.
    PID_INVALID   = 0b10000
    PID_CORE_MASK = 0b01111
    
    _name_map = {
        OUT:   'OUT',
        IN:    'IN',
        SOF:   'SOF',
        SETUP: 'SETUP',
        DATA0: 'DATA0',
        DATA1: 'DATA1',
        DATA2: 'DATA2',
        MDATA: 'MDATA',
        ACK:   'ACK',
        NAK:   'NAK',
        STALL: 'STALL',
        NYET:  'NYET',
        PRE:   'PRE',
        ERR:   'ERR',
        SPLIT: 'SPLIT',
        PING:  'PING',
    }
    
    _value_map = {v: k for k, v in _name_map.items()}

    # Member properties (LUNA parity).
    @property
    def name(self):
        """ Name of the PID, as a string (e.g. "DATA1"). """
        return USBPacketID._name_map.get(int(self) & USBPacketID.PID_CORE_MASK, 'UNKNOWN')

    @property
    def value(self):
        """ Value of the PID, as an integer. """
        return int(self)

    @classmethod
    def from_byte(cls, byte, skip_checks=False):
        """ Creates a PID object from a byte. """
        if isinstance(byte, bytes):
            pid_as_int = int.from_bytes(byte, byteorder='little')
        else:
            pid_as_int = byte
        return cls.from_int(pid_as_int, skip_checks=skip_checks)
    
    @classmethod
    def from_int(cls, value, skip_checks=True):
        """ Create a PID object from an integer. """
        PID_MASK           = 0b1111
        INVERTED_PID_SHIFT = 4
        
        # Pull out the PID and its inverse from the byte.
        pid          = value & PID_MASK
        inverted_pid = value >> INVERTED_PID_SHIFT
        
        # If we're not skipping checks,
        if not skip_checks:
            if (pid ^ inverted_pid) != PID_MASK:
                pid |= cls.PID_INVALID
        
        return cls(pid)
    
    @classmethod
    def from_name(cls, name):
        """ Create a PID object from a string representation of its name. """
        return getattr(cls, name.upper())
    
    @classmethod
    def parse(cls, value):
        """ Attempt to create a PID object from a number, byte, or string. """
        if isinstance(value, bytes):
            return cls.from_byte(value)
        
        if isinstance(value, str):
            return cls.from_name(value)
        
        if isinstance(value, int):
            return cls.from_int(value)
        
        return value
    
    # PID classification. Written as member methods (``pid.is_data()``) that also work when called
    # with the PID as first argument (``USBPacketID.is_data(pid)``): method bodies only use integer
    # operations on self and reference the class explicitly.
    def category(self):
        """ Returns the USBPIDCategory that each given PID belongs to. """
        return self & USBPIDCategory.MASK
    
    def is_data(self):
        """ Returns true iff the given PID represents a DATA packet. """
        return USBPacketID.category(self) == USBPIDCategory.DATA
    
    def is_token(self):
        """ Returns true iff the given PID represents a token packet. """
        return USBPacketID.category(self) == USBPIDCategory.TOKEN
    
    def is_handshake(self):
        """ Returns true iff the given PID represents a handshake packet. """
        return USBPacketID.category(self) == USBPIDCategory.HANDSHAKE
    
    def is_invalid(self):
        """ Returns true if this object is an attempt to encapsulate an invalid PID. """
        return bool(self & USBPacketID.PID_INVALID)
    
    def direction(self):
        """ Get a USB direction from a PacketID. """
        if self == USBPacketID.SOF:
            return None
        
        if self == USBPacketID.SETUP or self == USBPacketID.OUT:
            return USBDirection.OUT
        
        if self == USBPacketID.IN:
            return USBDirection.IN
        
        raise ValueError("cannot determine the direction of a non-token PID")
    
    def summarize(self):
        """ Return a summary of the given packet. """
        # By default, get the raw name.
        core_pid = self & USBPacketID.PID_CORE_MASK
        name = USBPacketID._name_map.get(core_pid, 'UNKNOWN')
        
        if USBPacketID.is_invalid(self):
            return "{} (check-nibble invalid)".format(name)
        else:
            return name
    
    def byte(self):
        """ Return the value with its upper nibble. """
        inverted_pid = self ^ 0b1111
        full_pid     = (inverted_pid << 4) | self
        
        return full_pid


# Expose the PID constants as member instances, so that the member-level API (leaf.byte(),
# leaf.is_data(), leaf.name, ...) is available on USBPacketID.OUT, USBPacketID.DATA1, etc.
for _pid_name in ("OUT", "IN", "SOF", "SETUP", "DATA0", "DATA1", "DATA2", "MDATA",
                  "ACK", "NAK", "STALL", "NYET", "PRE", "ERR", "SPLIT", "PING"):
    setattr(USBPacketID, _pid_name, USBPacketID(getattr(USBPacketID, _pid_name)))
del _pid_name
