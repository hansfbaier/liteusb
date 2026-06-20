#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause
""" Utilities for building USB descriptors into gateware. """

import struct
import functools

from migen import *
from migen.genlib.fsm import FSM, NextState

from ..stream import USBInStreamInterface
from ...utils.bus import OneHotMultiplexer


class USBDescriptorStreamGenerator(Module):
    """ Specialized stream generator for generating USB descriptor constants. """

    def __init__(self, data, domain="usb"):
        """
        Parameters:
            data -- The raw bytes for the descriptor.
        """
        self._data = data
        self._data_length = len(data)
        self._domain = domain

        #
        # I/O port.
        #
        self.start = Signal()
        self.done = Signal()
        self.start_position = Signal(max=self._data_length)
        self.max_length = Signal(16)

        self.stream = USBInStreamInterface()

        # Create ROM to store descriptor data
        self.specials.rom = Memory(8, self._data_length, init=data)
        rom_read_port = self.rom.get_port()
        self.specials += rom_read_port

        # Signals
        position_in_stream = Signal(max=self._data_length + 1)
        bytes_sent = Signal(16)

        # Track first and last packet
        on_first_packet = Signal()
        on_last_packet = Signal()

        self.comb += on_first_packet.eq(position_in_stream == self.start_position)

        # FSM for stream generation
        fsm = FSM(reset_state="IDLE")
        fsm = ClockDomainsRenamer("usb")(fsm)
        self.submodules += fsm

        fsm.act("IDLE",
            rom_read_port.adr.eq(self.start_position),
            NextValue(position_in_stream, self.start_position),
            NextValue(bytes_sent, 0),
            If(self.start,
                If(self.max_length > 0,
                    NextState("STREAMING")
                ).Else(
                    NextState("DONE")
                )
            )
        )

        fsm.act("STREAMING",
            rom_read_port.adr.eq(position_in_stream),
            self.stream.valid.eq(1),
            self.stream.payload.eq(rom_read_port.dat_r),
            self.stream.first.eq(on_first_packet),
            self.stream.last.eq(on_last_packet),

            If(self.stream.ready,
                If(~on_last_packet,
                    NextValue(position_in_stream, position_in_stream + 1),
                    NextValue(bytes_sent, bytes_sent + 1),
                    rom_read_port.adr.eq(position_in_stream + 1)
                ).Else(
                    NextState("DONE")
                )
            )
        )

        fsm.act("DONE",
            self.done.eq(1),
            NextState("IDLE")
        )

        # on_last_packet depends on bytes_sent, so compute it in comb
        self.comb += on_last_packet.eq(
            (position_in_stream >= self._data_length - 1) |
            (bytes_sent + 1 >= self.max_length)
        )


class GetDescriptorHandlerDistributed(Module):
    """ Gateware that handles responding to GetDescriptor requests.

    Currently does not support descriptors in multiple languages.

    I/O port:
        I: value[16]  -- The value field associated with the Get Descriptor request.
                         Contains the descriptor type and index.
        I: length[16] -- The length field associated with the Get Descriptor request.
                         Determines the maximum amount allowed in a response.

        I: start      -- Strobe that indicates when a descriptor should be transmitted.

        *: tx         -- The USBInStreamInterface that streams our descriptor data.
        O: stall      -- Pulsed if a STALL handshake should be generated, instead of a response.
    """

    def __init__(self, descriptor_collection, max_packet_length=64):
        """
        Parameters:
            descriptor_collection -- The DeviceDescriptorCollection containing the descriptors
                                     to use for this device.
        """
        self._descriptors = descriptor_collection
        self._max_packet_length = max_packet_length

        #
        # I/O port
        #
        self.value = Signal(16)
        self.length = Signal(16)
        self.start = Signal()
        self.start_position = Signal(11)
        self.tx = USBInStreamInterface()
        self.stall = Signal()

        # Collection that will store each of our descriptor-generation submodules.
        self._descriptor_generators = {}

        #
        # Figure out the maximum length we're willing to send.
        #
        self.length_internal = Signal(16)
        words_remaining = Signal(16)

        self.comb += words_remaining.eq(self.length - self.start_position)
        self.comb += [
            If(words_remaining <= self._max_packet_length,
                self.length_internal.eq(words_remaining)
            ).Else(
                self.length_internal.eq(self._max_packet_length)
            )
        ]

    def do_finalize(self):
        #
        # Create our constant-stream generators for each of our descriptors.
        #
        for type_number, index, raw_descriptor in self._descriptors:
            # Create the generator...
            if isinstance(raw_descriptor, bytes):
                generator = USBDescriptorStreamGenerator(raw_descriptor, domain="usb")
            else:
                generator = raw_descriptor()
            self._descriptor_generators[(type_number, index)] = generator

            # Connect generator signals
            self.comb += [
                generator.max_length.eq(self.length_internal),
                generator.start_position.eq(self.start_position)
            ]

            # Attach it to this module
            type_ref = str(type_number)
            if hasattr(type_number, 'name'):
                type_ref = type_number.name
            setattr(self.submodules, f'gen_{type_ref}_{index}', generator)

        #
        # Connect up each of our generators using a Case statement
        #
        cases = {}
        stall_exprs = []

        for (type_number, index), generator in self._descriptor_generators.items():
            case_value = (type_number << 8) | index

            # Create connections for this case
            cases[case_value] = [
                self.tx.payload.eq(generator.stream.payload),
                self.tx.valid.eq(generator.stream.valid),
                self.tx.first.eq(generator.stream.first),
                self.tx.last.eq(generator.stream.last),
                generator.stream.ready.eq(self.tx.ready),
                generator.start.eq(self.start),
            ]
            stall_exprs.append((self.value == case_value) & ~self.start)

        # Add default case for stall - stall if start is asserted but no case matches
        if cases:
            from migen.genlib.coding import Case
            self.comb += Case(self.value, cases)

        # Stall if no descriptor matches and we have a start
        any_match = Signal()
        self.comb += any_match.eq(0)
        for (type_number, index) in self._descriptor_generators.keys():
            case_value = (type_number << 8) | index
            self.comb += any_match.eq(any_match | (self.value == case_value))

        self.comb += self.stall.eq(self.start & ~any_match)


class GetDescriptorHandlerBlock(Module):
    """ Gateware that handles responding to GetDescriptor requests.

    Currently does not support descriptors in multiple languages.

    I/O port:
        I: value[16]      -- The value field associated with the Get Descriptor request.
                             Contains the descriptor type and index.
        I: length[16]     -- The length field associated with the Get Descriptor request.
                             Determines the maximum amount allowed in a response.

        I: start          -- Strobe that indicates when a descriptor should be transmitted.
        I: start_position -- Specifies the starting position of the descriptor data to be transmitted.

        *: tx             -- The USBInStreamInterface that streams our descriptor data.
        O: stall          -- Pulsed if a STALL handshake should be generated, instead of a response.
    """

    ELEMENT_SIZE = 4
    COUNT_SIZE_BITS = 16
    ADDRESS_SIZE_BITS = 16

    def __init__(self, descriptor_collection, max_packet_length=64, domain="usb"):
        """
        Parameters
        ----------
        descriptor_collection: DeviceDescriptorCollection
            The DeviceDescriptorCollection containing the descriptors to use for this device.
        max_packet_length: int
            Maximum packet length.
        domain: string
            The clock domain this generator should belong to. Defaults to 'usb'.
        """
        self._descriptors = descriptor_collection
        self._max_packet_length = max_packet_length
        self._domain = domain

        #
        # I/O port
        #
        self.value = Signal(16)
        self.length = Signal(16)
        self.start = Signal()
        self.start_position = Signal(11)
        self.tx = USBInStreamInterface()
        self.stall = Signal()

    @classmethod
    def _align_to_element_size(cls, n):
        """ Returns a given number rounded up to the next "aligned" element size. """
        return (n + (cls.ELEMENT_SIZE - 1)) // cls.ELEMENT_SIZE

    def generate_rom_content(self):
        """ Generates the contents of the ROM used to hold descriptors. """
        # Get all descriptors and cache them in a dictionary
        descriptors = {}
        for type_number, index, raw_descriptor in self._descriptors:
            if type_number not in descriptors:
                descriptors[type_number] = {}
            descriptors[type_number][index] = raw_descriptor

        # Check if we need to support non-consecutive indexes
        indirect_idx = False
        for type_number, indexes in sorted(descriptors.items()):
            if max(indexes.keys()) != len(indexes) - 1:
                indirect_idx = True
                break

        #
        # Compute the ROM size
        #
        max_type_number = max(descriptors.keys())
        max_descriptor_size = 0

        rom_size_table_pointers = (max_type_number + 1) * self.ELEMENT_SIZE
        table_entry_count = functools.reduce(lambda x, indexes: x + len(indexes), descriptors.values(), 0)
        rom_size_table_entries = table_entry_count * self.ELEMENT_SIZE

        rom_size_descriptors = 0
        for descriptor_set in descriptors.values():
            for raw_descriptor in descriptor_set.values():
                aligned_size = self._align_to_element_size(len(raw_descriptor))
                rom_size_descriptors += aligned_size * self.ELEMENT_SIZE
                max_descriptor_size = max(max_descriptor_size, len(raw_descriptor))

        total_size = rom_size_table_pointers + rom_size_table_entries + rom_size_descriptors
        rom = bytearray(total_size)

        #
        # Fill the ROM
        #
        next_free_address = (max_type_number + 1) * self.ELEMENT_SIZE
        type_index_base_address = [0] * (max_type_number + 1)

        # Generate table pointers
        for type_number, indexes in sorted(descriptors.items()):
            pointer_bytes = struct.pack(">HH", len(indexes), next_free_address)
            type_base_address = type_number * self.ELEMENT_SIZE
            rom[type_base_address:type_base_address + self.ELEMENT_SIZE] = pointer_bytes
            type_index_base_address[type_number] = next_free_address
            next_free_address += len(indexes) * self.ELEMENT_SIZE

        index_map = {}

        # Create index tables and add descriptors
        for type_number, descriptor_set in sorted(descriptors.items()):
            for i, (index, raw_descriptor) in enumerate(sorted(descriptor_set.items())):
                pointer_bytes = struct.pack(">HH", len(raw_descriptor), next_free_address)
                index_base_address = type_index_base_address[type_number] + i * self.ELEMENT_SIZE
                rom[index_base_address:index_base_address + 4] = pointer_bytes
                rom[next_free_address:next_free_address + len(raw_descriptor)] = raw_descriptor

                aligned_size = self._align_to_element_size(len(raw_descriptor))
                next_free_address += aligned_size * self.ELEMENT_SIZE

                if indirect_idx:
                    index_map[index | (type_number << 8)] = i

        assert total_size == len(rom)

        #
        # Convert ROM to initializer
        #
        total_elements = total_size // self.ELEMENT_SIZE
        element_size = self.ELEMENT_SIZE

        rom_entries = (rom[(element_size * i):(element_size * i) + element_size] for i in range(total_elements))
        initializer = [struct.unpack(">I", rom_entry)[0] for rom_entry in rom_entries]

        return initializer, max_descriptor_size, max_type_number, index_map

    def do_finalize(self):
        # Aliases for type/index
        type_number = Signal(8)
        index = Signal(8)

        self.comb += [
            index.eq(self.value[0:8]),
            type_number.eq(self.value[8:16])
        ]

        #
        # Create the ROM
        #
        rom_content, descriptor_max_length, max_type_index, index_map = self.generate_rom_content()

        self.specials.rom = Memory(32, len(rom_content), init=rom_content)
        rom_read_port = self.rom.get_port()
        self.specials += rom_read_port

        # Convenience aliases - ROM data format is (count, pointer) for metadata
        # Upper 16 bits = count, lower 16 bits = pointer
        rom_element_count = rom_read_port.dat_r[16:32]
        # Pointer is in bytes, but we need word address for Memory port
        rom_element_pointer = rom_read_port.dat_r[2:2+len(rom_read_port.adr)]

        #
        # Maximum length calculation
        #
        length = Signal(16)
        words_remaining = Signal(16)

        self.comb += words_remaining.eq(self.length - self.start_position)

        # Handle domain-specific sync logic
        if self._domain == "sync":
            self.sync += [
                If(words_remaining <= self._max_packet_length,
                    length.eq(words_remaining)
                ).Else(
                    length.eq(self._max_packet_length)
                )
            ]
        else:
            # For other domains, use the domain's sync
            domain_sync = getattr(self.sync, self._domain)
            domain_sync += [
                If(words_remaining <= self._max_packet_length,
                    length.eq(words_remaining)
                ).Else(
                    length.eq(self._max_packet_length)
                )
            ]

        # Position tracking
        position_in_stream = Signal(max=descriptor_max_length + 1)
        bytes_sent = Signal(16)

        descriptor_length = Signal(16)
        descriptor_data_base_address = Signal(len(rom_read_port.adr))

        on_first_packet = Signal()
        on_last_packet = Signal()

        self.comb += on_first_packet.eq(position_in_stream == self.start_position)
        self.comb += on_last_packet.eq(
            (position_in_stream == (descriptor_length - 1)) |
            (bytes_sent + 1 >= length)
        )

        # Handle index mapping for non-consecutive indexes
        descr_idx = Signal(8)
        if len(index_map) != 0:
            # Create switch case for index mapping
            from migen.genlib.coding import Case
            idx_cases = {}
            for orig_idx, remapped_idx in index_map.items():
                idx_cases[orig_idx] = [descr_idx.eq(remapped_idx)]
            idx_cases["default"] = [descr_idx.eq(0xFF)]

            if self._domain == "sync":
                self.sync += Case(Cat(index, type_number), idx_cases)
            else:
                domain_sync = getattr(self.sync, self._domain)
                domain_sync += Case(Cat(index, type_number), idx_cases)
        else:
            self.comb += descr_idx.eq(index)

        #
        # FSM
        #
        fsm = FSM(reset_state="IDLE")
        # Explicitly place FSM in the usb domain to match the test clock
        fsm = ClockDomainsRenamer(self._domain)(fsm)
        self.submodules += fsm

        fsm.act("IDLE",
            NextValue(bytes_sent, 0),
            rom_read_port.adr.eq(type_number),
            If(self.start,
                NextState("START")
            )
        )

        fsm.act("START",
            rom_read_port.adr.eq(type_number),
            NextValue(position_in_stream, self.start_position),
            If(type_number <= max_type_index,
                NextState("LOOKUP_TYPE")
            ).Else(
                self.stall.eq(1),
                NextState("IDLE")
            )
        )

        fsm.act("LOOKUP_TYPE",
            If(descr_idx >= rom_element_count,
                self.stall.eq(1),
                NextState("IDLE")
            ).Else(
                rom_read_port.adr.eq(rom_element_pointer + descr_idx),
                If(length == 0,
                    NextState("SEND_ZLP")
                ).Else(
                    NextState("LOOKUP_DESCRIPTOR")
                )
            )
        )

        fsm.act("LOOKUP_DESCRIPTOR",
            rom_read_port.adr.eq((rom_read_port.dat_r + position_in_stream) >> 2),
            NextValue(descriptor_data_base_address, rom_element_pointer),
            NextValue(descriptor_length, rom_element_count),
            If(position_in_stream >= rom_element_count,
                NextState("SEND_ZLP")
            ).Else(
                NextState("SEND_DESCRIPTOR")
            )
        )

        word_in_stream = Signal(9)  # Enough bits for position >> 2
        byte_in_stream = Signal(2)

        self.comb += [
            word_in_stream.eq(position_in_stream >> 2),
            byte_in_stream.eq(position_in_stream[0:2])
        ]

        fsm.act("SEND_DESCRIPTOR",
            self.tx.valid.eq(1),
            rom_read_port.adr.eq(descriptor_data_base_address + word_in_stream),
            # Byte select: reverse byte order within word using part()
            self.tx.payload.eq(rom_read_port.dat_r.part((3 - byte_in_stream) * 8, 8)),
            self.tx.first.eq(on_first_packet),
            self.tx.last.eq(on_last_packet),

            If(self.tx.ready,
                If(~on_last_packet,
                    NextValue(position_in_stream, position_in_stream + 1),
                    NextValue(bytes_sent, bytes_sent + 1),
                    rom_read_port.adr.eq(descriptor_data_base_address + ((position_in_stream + 1) >> 2))
                ).Else(
                    NextValue(descriptor_length, 0),
                    NextValue(descriptor_data_base_address, 0),
                    NextState("IDLE")
                )
            )
        )

        fsm.act("SEND_ZLP",
            self.tx.valid.eq(1),
            self.tx.last.eq(1),
            NextState("IDLE")
        )


class GetDescriptorHandlerMux(Module):
    """ Multiplexer for multiple descriptor handlers. """

    def __init__(self, domain="usb"):
        self._domain = domain
        self._handlers = []

        #
        # I/O port
        #
        self.value = Signal(16)
        self.length = Signal(16)
        self.start = Signal()
        self.start_position = Signal(11)
        self.tx = USBInStreamInterface()
        self.stall = Signal()

    def add_descriptor_handler(self, handler):
        self._handlers.append(handler)

    def do_finalize(self):
        self.submodules += self._handlers

        # Create stall signals for each handler
        stalled = [Signal() for _ in range(len(self._handlers))]

        # Stall when all handlers stall
        if len(self._handlers) > 0:
            all_stalled = functools.reduce(lambda a, b: a & b, stalled)
            self.comb += self.stall.eq(all_stalled)
        else:
            self.comb += self.stall.eq(0)

        # Connect inputs to each handler and handle stall latches
        for i, handler in enumerate(self._handlers):
            self.comb += [
                handler.value.eq(self.value),
                handler.length.eq(self.length),
                handler.start.eq(self.start),
                handler.start_position.eq(self.start_position),
            ]

            stall_latch = Signal()
            self.comb += stalled[i].eq(handler.stall | stall_latch)

            if self._domain == "sync":
                self.sync += [
                    If(self.start | self.stall,
                        stall_latch.eq(0)
                    ),
                    If(handler.stall & ~self.stall,
                        stall_latch.eq(1)
                    )
                ]
            else:
                domain_sync = getattr(self.sync, self._domain)
                domain_sync += [
                    If(self.start | self.stall,
                        stall_latch.eq(0)
                    ),
                    If(handler.stall & ~self.stall,
                        stall_latch.eq(1)
                    )
                ]

        # Create transmit multiplexer
        self.submodules.tx_mux = tx_mux = OneHotMultiplexer(
            interface_type=USBInStreamInterface,
            mux_signals=('payload',),
            or_signals=('valid', 'first', 'last'),
            pass_signals=('ready',)
        )

        for handler in self._handlers:
            tx_mux.add_interface(handler.tx)

        self.comb += [
            self.tx.payload.eq(tx_mux.output.payload),
            self.tx.valid.eq(tx_mux.output.valid),
            self.tx.first.eq(tx_mux.output.first),
            self.tx.last.eq(tx_mux.output.last),
            tx_mux.output.ready.eq(self.tx.ready)
        ]
