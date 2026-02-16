#!/usr/bin/env python3
"""
Verify the CRC5 calculation for USB token packets.

According to USB spec, a token packet contains:
- PID (8 bits): 4-bit PID + 4-bit complement
- Token data (11 bits): 7-bit address + 4-bit endpoint  
- CRC5 (5 bits): Computed over the 11-bit token data

The token is transmitted as 3 bytes:
- Byte 1: PID
- Byte 2: ADDR[6:0] | ENDP[0] (bit 7 is part of CRC? No...)
- Byte 3: ENDP[3:1] | CRC5[4:0]

Actually, let me re-read the spec...
The 11-bit token data + 5-bit CRC = 16 bits = 2 bytes exactly.

Layout:
- Bits [10:4] = Address (7 bits)
- Bits [3:0]  = Endpoint (4 bits)
- These are protected by CRC5

So the bytes are:
- Byte 2 (first data byte): bits [7:0] of the 16-bit field
  - But wait, 11 bits don't fit evenly into bytes...
  
Actually, looking at the code:
- Cat(token_data[0:8], rx_data[0:3]) creates an 11-bit value
  - token_data[0:8] = 8 bits from first data byte (address)
  - rx_data[0:3] = 3 bits from second data byte (endpoint LSBs)
- rx_data[3:8] = 5 bits from second data byte (CRC)

This means:
- Byte 2 contains: ADDR[6:0] in bits [6:0], and ENDP[0] in bit 7? No...
- Byte 3 contains: ENDP[3:1] in bits [2:0], and CRC5[4:0] in bits [7:3]

Wait, that would mean:
- Byte 2: ADDR[6:0] in [6:0], and ??? in [7]
- Byte 3: ENDP[3:1] in [2:0], CRC5[4:0] in [7:3]

But that doesn't account for ENDP[0]!

Let me re-read the code more carefully:
```python
expected_crc = self._generate_crc_for_token(
    Cat(token_data[0:8], self.utmi.rx_data[0:3]))
```

This concatenates:
- token_data[0:8]: the first byte received (stored from previous cycle)
- self.utmi.rx_data[0:3]: lower 3 bits of the second byte

So the 11-bit token is:
- Bits [10:3] = first byte received (8 bits) = address
- Bits [2:0] = lower 3 bits of second byte = endpoint[2:0]

But endpoint is 4 bits! Where is endpoint[3]?

Looking at the CRC check:
```python
If(self.utmi.rx_data[3:8] == expected_crc,
```

This extracts bits [7:3] of the second byte (5 bits) as the CRC.

So byte 3 (second data byte) layout is:
- Bits [2:0] = endpoint[2:0] (3 bits)
- Bits [7:3] = CRC5[4:0] (5 bits)

But where is endpoint[3] (the MSB)? It seems to be missing!

Actually, wait. Let me check if maybe the endpoint is only 3 bits in the CRC calculation
but 4 bits are extracted later. Looking at the code:

```python
NextValue(Cat(self.interface.address, self.interface.endpoint), token_data),
```

This stores token_data into {address, endpoint}. token_data is 11 bits.
If address is 7 bits, then endpoint gets 11-7 = 4 bits. So endpoint is 4 bits.

But then where does endpoint[3] come from? It seems like the code might have a bug
where it only uses 3 bits for endpoint in the CRC calculation but stores 4 bits!

Let me verify this hypothesis by computing the CRC with different endpoint values.
"""

import functools
import operator

def xor_bits(token, *indices):
    """XOR bits at given indices (counting from MSB = index 0)."""
    bits = (token[len(token) - 1 - i] for i in indices)
    return functools.reduce(operator.__xor__, bits)

def generate_crc5(token_value):
    """Generate CRC5 for an 11-bit token using the same algorithm as the code."""
    # Convert to list of bits (MSB first)
    bits = [(token_value >> i) & 1 for i in range(10, -1, -1)]
    
    # CRC5 polynomial from USB spec
    crc = [0] * 5
    crc[0] = xor_bits(bits, 10, 9, 8, 5, 4, 2)
    crc[1] = ~xor_bits(bits, 10, 9, 8, 7, 4, 3, 1) & 1
    crc[2] = xor_bits(bits, 10, 9, 8, 7, 6, 3, 2, 0)
    crc[3] = xor_bits(bits, 10, 7, 6, 4, 1)
    crc[4] = xor_bits(bits, 10, 9, 6, 5, 3, 0)
    
    # Combine into 5-bit value
    crc_value = (crc[4] << 4) | (crc[3] << 3) | (crc[2] << 2) | (crc[1] << 1) | crc[0]
    return crc_value

# Test data from the test
byte1 = 0b11100001  # 0xE1 = OUT PID
byte2 = 0b00111010  # 0x3A = address
byte3 = 0b00111101  # 0x3D = endpoint + CRC

print("Test data:")
print(f"  Byte 1 (PID): 0x{byte1:02X} = 0b{byte1:08b}")
print(f"  Byte 2 (addr): 0x{byte2:02X} = 0b{byte2:08b}")
print(f"  Byte 3 (ep+crc): 0x{byte3:02X} = 0b{byte3:08b}")

# The code extracts:
# - Address = byte2 = 0x3A
# - Endpoint[2:0] = byte3[2:0] = 0b101 = 5
# - CRC5 = byte3[7:3] = 0b00111 = 7

addr = byte2
ep_3bit = byte3 & 0x07  # 3 bits: [2:0]
crc_from_data = (byte3 >> 3) & 0x1F  # 5 bits: [7:3]

print(f"\nExtracted values:")
print(f"  Address = 0x{addr:02X} = {addr}")
print(f"  Endpoint (3 bits) = 0b{ep_3bit:03b} = {ep_3bit}")
print(f"  CRC5 from data = 0b{crc_from_data:05b} = {crc_from_data}")

# The token for CRC calculation is:
# token = {byte2, byte3[2:0]} = 0x3A concatenated with 3-bit endpoint
token_3bit_ep = (addr << 3) | ep_3bit
print(f"\nToken for CRC (using 3-bit endpoint): 0b{token_3bit_ep:011b} = 0x{token_3bit_ep:03X}")
crc_expected_3bit = generate_crc5(token_3bit_ep)
print(f"  Computed CRC5 = 0b{crc_expected_3bit:05b} = {crc_expected_3bit}")

if crc_expected_3bit == crc_from_data:
    print("  *** MATCH! ***")
else:
    print("  *** MISMATCH! ***")

# What if endpoint is 4 bits?
ep_4bit = byte3 & 0x0F  # 4 bits: [3:0]
print(f"\nEndpoint (4 bits) = 0b{ep_4bit:04b} = 0x{ep_4bit:X} = {ep_4bit}")

# For a 4-bit endpoint, the token would be:
# token = {byte2[6:0], byte3[3:0]} = 7-bit addr + 4-bit ep = 11 bits
token_4bit_ep = ((addr & 0x7F) << 4) | ep_4bit
print(f"Token for CRC (using 4-bit endpoint): 0b{token_4bit_ep:011b} = 0x{token_4bit_ep:03X}")
crc_expected_4bit = generate_crc5(token_4bit_ep)
print(f"  Computed CRC5 = 0b{crc_expected_4bit:05b} = {crc_expected_4bit}")

if crc_expected_4bit == crc_from_data:
    print("  *** MATCH! ***")
else:
    print("  *** MISMATCH! ***")

# What CRC should be in the data for the expected endpoint 0xA?
print(f"\n\n=== Expected endpoint 0xA ===")
expected_ep = 0xA  # 10 = 0b1010
# For 3-bit extraction: only bits [2:0] = 0b010 = 2
token_expected_ep = (addr << 3) | (expected_ep & 0x07)
crc_expected_ep = generate_crc5(token_expected_ep)
print(f"For addr=0x{addr:02X}, ep=0x{expected_ep:X} (using 3-bit ep={expected_ep & 0x07}):")
print(f"  Token = 0b{token_expected_ep:011b}")
print(f"  CRC5 = 0b{crc_expected_ep:05b} = {crc_expected_ep}")
print(f"  Byte 3 should be: 0b{crc_expected_ep:05b}_{expected_ep & 0x07:03b} = 0x{((crc_expected_ep << 3) | (expected_ep & 0x07)):02X}")

# So the test data has:
# - Address 0x3A (58)
# - Endpoint 0xD (13) if using 4 bits, or 0x5 (5) if using 3 bits
# - CRC 0b00111 = 7
# 
# But the test expects endpoint 0xA (10)!
# 
# Let's check what CRC we'd get for addr=0x3A, ep=0x5 (the 3-bit endpoint value in the data):
print(f"\n=== Actual endpoint in test data (3-bit value) ===")
actual_ep_3bit = byte3 & 0x07  # 0b101 = 5
token_actual_ep = (addr << 3) | actual_ep_3bit
crc_actual_ep = generate_crc5(token_actual_ep)
print(f"For addr=0x{addr:02X}, ep=0x{actual_ep_3bit:X} (3-bit):")
print(f"  Token = 0b{token_actual_ep:011b}")
print(f"  CRC5 = 0b{crc_actual_ep:05b} = {crc_actual_ep}")
print(f"  CRC5 from data = 0b{crc_from_data:05b} = {crc_from_data}")
if crc_actual_ep == crc_from_data:
    print("  This matches the CRC in the data!")
    print(f"  So the test data has endpoint 0x{actual_ep_3bit:X}, NOT 0xA!")
else:
    print("  Still doesn't match!")

# Now let's figure out what the correct test data should be
print(f"\n\n=== CORRECT TEST DATA ===")
print(f"For addr=0x3A, ep=0xA:")
correct_byte2 = 0x3A
correct_ep_lower3 = expected_ep & 0x07  # 0b010 = 2
correct_crc = crc_expected_ep
correct_byte3 = (correct_crc << 3) | correct_ep_lower3
print(f"  Byte 2: 0x{correct_byte2:02X} = 0b{correct_byte2:08b}")
print(f"  Byte 3: 0x{correct_byte3:02X} = 0b{correct_byte3:08b}")
print(f"  Full token: 0b11100001_{correct_byte2:08b}_{correct_byte3:08b}")

# Convert to the format used in the test
print(f"\n  Test should use:")
print(f"    yield from self.provide_packet(0b11100001, 0b{correct_byte2:08b}, 0b{correct_byte3:08b})")
print(f"    or:")
print(f"    yield from self.provide_packet(0x{byte1:02X}, 0x{correct_byte2:02X}, 0x{correct_byte3:02X})")
