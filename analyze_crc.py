#!/usr/bin/env python3
import functools
import operator

def generate_crc5(token_bits):
    """Generate USB CRC5 for an 11-bit token."""
    # Token should be 11 bits: [10:4] = address (7 bits), [3:0] = endpoint (4 bits)
    # CRC5 polynomial: G(x) = x^5 + x^2 + 1 (0b00101)
    # Remainder should be 0b01111
    
    # Extend to 16 bits for calculation
    token = token_bits & 0x7FF  # 11 bits
    
    # CRC5 polynomial: x^5 + x^2 + 1 = 0b00101 = 5
    # We need to shift left by 5 and divide
    dividend = token << 5
    polynomial = 0b101001  # x^5 + x^2 + 1 = 0x21, but we use 6 bits for the algorithm
    
    # Perform polynomial division
    remainder = dividend
    for i in range(10, -1, -1):
        if remainder & (1 << (i + 5)):
            remainder ^= polynomial << i
    
    crc5 = remainder & 0x1F
    
    # Invert the CRC (USB standard)
    crc5_inverted = (~crc5) & 0x1F
    
    return crc5_inverted

# According to the test comment:
# out to 0x3a, endpoint 0xa => 0xE1 5C BC
# But the test uses: 0b11100001, 0b00111010, 0b00111101

# Test data from the test:
test_byte1 = 0b11100001  # 0xE1 = OUT PID
test_byte2 = 0b00111010  # 0x3A = address
test_byte3 = 0b00111101  # 0x3D = endpoint[0:3]=0b101=5, CRC5=0b110=6

print("Test data analysis:")
print(f"  Byte 1 (PID): 0x{test_byte1:02X} = 0b{test_byte1:08b}")
print(f"    PID = 0b{test_byte1 & 0x0F:04b} = {test_byte1 & 0x0F}")
print(f"    Check = 0b{(test_byte1 >> 4) & 0x0F:04b} = {(test_byte1 >> 4) & 0x0F}")
print(f"    PID valid: {(test_byte1 & 0x0F) == (~((test_byte1 >> 4) & 0x0F) & 0x0F)}")

print(f"\n  Byte 2 (Addr): 0x{test_byte2:02X} = 0b{test_byte2:08b}")
print(f"    Address = 0x{test_byte2:02X} = {test_byte2}")

print(f"\n  Byte 3 (EP+CRC): 0x{test_byte3:02X} = 0b{test_byte3:08b}")
print(f"    Endpoint[0:3] = 0b{test_byte3 & 0x07:03b} = {test_byte3 & 0x07}")
print(f"    Endpoint[0:4] should be extracted from bits [0:4] = 0b{test_byte3 & 0x0F:04b} = {test_byte3 & 0x0F}")
print(f"    CRC5 = 0b{(test_byte3 >> 3) & 0x1F:05b} = {(test_byte3 >> 3) & 0x1F}")

# Expected data according to comment:
expected_byte1 = 0xE1
expected_byte2 = 0x5C
expected_byte3 = 0xBC

print(f"\n\nExpected data (from comment):")
print(f"  Byte 1 (PID): 0x{expected_byte1:02X} = 0b{expected_byte1:08b}")
print(f"  Byte 2 (Addr): 0x{expected_byte2:02X} = 0b{expected_byte2:08b}")
print(f"  Byte 3 (EP+CRC): 0x{expected_byte3:02X} = 0b{expected_byte3:08b}")
print(f"    Endpoint = 0b{expected_byte3 & 0x0F:04b} = {expected_byte3 & 0x0F}")
print(f"    CRC5 = 0b{(expected_byte3 >> 4) & 0x1F:05b} = {(expected_byte3 >> 4) & 0x1F}")

# Verify what the address should be from expected data
expected_addr = expected_byte2
print(f"\n  Expected address = 0x{expected_addr:02X} = {expected_addr}")

# Actually, let me re-read the USB token format
# Token packet format:
#   PID (8 bits) + Token Data (11 bits) + CRC5 (5 bits) = 24 bits = 3 bytes
#   Token Data = Address (7 bits) + Endpoint (4 bits)
#   Byte 1: PID
#   Byte 2: ADDR[6:0] | ENDP[0]  (bit 7 is always 0?)
#   Byte 3: ENDP[3:1] | CRC5[4:0]

# Wait, that's not right either. Let me check the actual bit layout.
# According to USB spec, token data is:
#   Bits [10:4] = Address (7 bits)
#   Bits [3:0]  = Endpoint (4 bits)
#   Bits [4:0] of byte 3 = CRC5 (but this overlaps...)

# Actually, let me check: the token data is 11 bits followed by 5 CRC bits
# That's 16 bits = 2 bytes
# So the layout is:
#   Byte 1: PID
#   Byte 2: ADDR[6:0] in bits [6:0], bit 7 is?
#   Byte 3: ENDP[3:0] in bits [3:0], CRC5[4:0] in bits [7:3]?

# But that doesn't work because CRC5 is 5 bits and would overlap with endpoint

# Let me check a different interpretation:
# The 11-bit token field is: ADDR (7 bits) + ENDP (4 bits)
# This is sent LSB first, so:
#   Byte 2 contains: ADDR[6:0] (7 bits) + ENDP[0] (1 bit)
#   Byte 3 contains: ENDP[3:1] (3 bits) + CRC5[4:0] (5 bits)

# For address 0x3a = 0b0111010:
#   Byte 2 = 0b0_0111010 = 0x3A? But expected is 0x5C...

# Let me try: ADDR is sent LSB first
# ADDR = 0x3a = 0b0111010
# ADDR LSB first = 0b0101110 = 0x2E? 
# That doesn't match either.

# Actually, looking at the expected bytes 0xE1 0x5C 0xBC:
# 0x5C = 0b01011100 - could this be address 0x3a with some encoding?
# 0x3a = 0b00111010
# 0x5c = 0b01011100
# These don't look related by simple bit reversal...

# Let me try a different approach - work backwards from expected CRC
# If expected byte 3 is 0xBC = 0b10111100:
#   Bits [2:0] = 0b100 = 4? Or is it [3:0] for endpoint?
#   Bits [7:3] = 0b10111 = 23? That can't be CRC5 (max 31, but 23 is valid)

# Wait, I think I misread. Let me check USB spec more carefully.
# In USB token packets:
#   - PID is 8 bits (4 bits PID + 4 bits complement)
#   - The token-specific data is 11 bits: 7-bit address + 4-bit endpoint
#   - CRC5 is computed over these 11 bits
#   - Total after PID: 16 bits = 11 data + 5 CRC

# So the layout is:
#   Byte 2: Contains first 8 bits of (ADDR + ENDP + CRC5)
#   Byte 3: Contains remaining 8 bits

# But 11 + 5 = 16 bits = exactly 2 bytes. So:
#   Byte 2: ADDR[6:0] + ENDP[0]
#   Byte 3: ENDP[3:1] + CRC5[4:0]

# For address 0x3a = 0b011_1010:
#   Byte 2 should be: 0b0_0111010 = 0x3A for ADDR, but expected is 0x5C
# 
# Unless... the data is bit-reversed when transmitted?
# USB transmits LSB first, so within each byte, bits are reversed.

# Let me check: 0x3a bit reversed = 0x5c?
# 0x3a = 0b00111010, reversed = 0b01011100 = 0x5c. YES!

# So USB transmits LSB first within each byte. That means:
#   When we receive 0x5C, the actual value is 0x3A (bit reversed)
#   When we receive 0xBC, the actual value is 0x3D (bit reversed)

# But in the test, they're providing raw bytes without bit reversal!
# The test provides: 0xE1, 0x3A, 0x3D
# But should provide (according to comment): 0xE1, 0x5C, 0xBC

# Let me verify:
print("\n\nBit reversal check:")

def reverse_bits(byte):
    result = 0
    for i in range(8):
        if byte & (1 << i):
            result |= 1 << (7 - i)
    return result

print(f"0x3A reversed = 0x{reverse_bits(0x3A):02X}")
print(f"0x3D reversed = 0x{reverse_bits(0x3D):02X}")
print(f"0x5C reversed = 0x{reverse_bits(0x5C):02X}")
print(f"0xBC reversed = 0x{reverse_bits(0xBC):02X}")

# So if the test provides 0x3A, 0x3D, the actual token data after bit reversal is:
#   Address = reverse(0x3A) & 0x7F = 0x5C & 0x7F = 0x5C = 92
#   Endpoint = (reverse(0x3D) >> 7) & 0x01 | (reverse(0x3D) & 0x07) << 1?
#   Actually, let me think about this more carefully.

# The issue is that USBTokenDetector works on the data AFTER bit-unstuffing and NRZI decoding
# But it still receives bytes LSB first. So when the PHY receives 0x5C on the wire (LSB first),
# it assembles it as 0x3A in the register.

# So the test is correct in providing 0x3A (the value in the register after reception).
# The comment saying 0xE1 0x5C 0xBC might be referring to the on-wire format.

# But then why is the CRC check failing?

# Let me check what the CRC5 should be for address 0x3A with endpoint 0xA:
# Address = 0x3A = 0b0111010
# Endpoint = 0xA = 0b1010
# Token data = 0b0111010_1010 = 0x3AA = 938

# Compute CRC5 using the same algorithm as in the code
def xor_bits(token, *indices):
    bits = (token[len(token) - 1 - i] for i in indices)
    return functools.reduce(operator.__xor__, bits)

def compute_crc5_from_token(token_value):
    """Compute CRC5 using the same method as USBTokenDetector."""
    # Token is 11 bits
    
    # Use the same logic as in the USBTokenDetector code:
    # From the code:
    # return Cat(
    #      xor_bits(10, 9, 8, 5, 4, 2),
    #     ~xor_bits(10, 9, 8, 7, 4, 3, 1),
    #      xor_bits(10, 9, 8, 7, 6, 3, 2, 0),
    #      xor_bits(10, 7, 6, 4, 1),
    #      xor_bits(10, 9, 6, 5, 3, 0)
    # )
    
    bits = [(token_value >> i) & 1 for i in range(11)]
    
    crc = [0] * 5
    crc[0] = bits[10] ^ bits[9] ^ bits[8] ^ bits[5] ^ bits[4] ^ bits[2]
    crc[1] = ~(bits[10] ^ bits[9] ^ bits[8] ^ bits[7] ^ bits[4] ^ bits[3] ^ bits[1]) & 1
    crc[2] = bits[10] ^ bits[9] ^ bits[8] ^ bits[7] ^ bits[6] ^ bits[3] ^ bits[2] ^ bits[0]
    crc[3] = bits[10] ^ bits[7] ^ bits[6] ^ bits[4] ^ bits[1]
    crc[4] = bits[10] ^ bits[9] ^ bits[6] ^ bits[5] ^ bits[3] ^ bits[0]
    
    crc_value = (crc[4] << 4) | (crc[3] << 3) | (crc[2] << 2) | (crc[1] << 1) | crc[0]
    return crc_value

# For address 0x3A, endpoint 0xA:
# Token = {address[6:0], endpoint[3:0]} = {0x3A, 0xA} = 0b0111010_1010
token_val = (0x3A << 4) | 0x0A
print(f"\n\nCRC5 calculation for addr=0x3A, ep=0xA:")
print(f"  Token value = 0x{token_val:03X} = 0b{token_val:011b}")
crc5_expected = compute_crc5_from_token(token_val)
print(f"  Expected CRC5 = 0b{crc5_expected:05b} = {crc5_expected}")

# Now check what CRC is in the test data (0x3D):
# 0x3D = 0b00111101
# CRC5 is in bits [7:3] = 0b00111 = 7
test_crc5 = (0x3D >> 3) & 0x1F
print(f"\n  Test data CRC5 (from byte 0x3D) = 0b{test_crc5:05b} = {test_crc5}")

# Check if they match
if crc5_expected == test_crc5:
    print("  CRC5 MATCH!")
else:
    print("  CRC5 MISMATCH!")
    print(f"  The test data has incorrect CRC5 for addr=0x3A, ep=0xA")
    
    # What endpoint does the test data actually have?
    # From 0x3D = 0b00111101:
    # Endpoint = bits [3:0] = 0b1101 = 13 = 0xD
    test_ep = 0x3D & 0x0F
    print(f"\n  Test data endpoint = 0x{test_ep:X} = {test_ep}")
    
    # Compute CRC for addr=0x3A, ep=0xD:
    token_val_test = (0x3A << 4) | test_ep
    crc5_test = compute_crc5_from_token(token_val_test)
    print(f"  CRC5 for addr=0x3A, ep=0x{test_ep:X} = 0b{crc5_test:05b} = {crc5_test}")
    
    if crc5_test == test_crc5:
        print("  The test data has CRC5 for endpoint 0xD, not 0xA!")

# Now let's verify the expected bytes from the comment (0xE1, 0x5C, 0xBC):
print(f"\n\nVerifying expected data (0xE1, 0x5C, 0xBC):")
# 0x5C = 0b01011100
# Address = ? We need to figure out how to extract it

# In USB token format, the 11-bit field is:
#   Bits [10:4] = Address (7 bits)
#   Bits [3:0]  = Endpoint (4 bits)
# These 11 bits span bytes 2 and 3

# But bytes are received LSB first. So:
#   Byte 2 (0x5C = 0b01011100) after bit order correction: 0x3A
#   Byte 3 (0xBC = 0b10111100) after bit order correction: 0x3D

# Wait, that's the same as the test data! So the test data IS correct.
# The issue is the CRC5 computation or extraction.

# Let me re-examine the CRC5 extraction from the received bytes.
# In the code:
#   expected_crc = self._generate_crc_for_token(Cat(token_data[0:8], self.utmi.rx_data[0:3]))
#   If(self.utmi.rx_data[3:8] == expected_crc,

# So it extracts:
#   token_data[0:8] = first byte received = 0x3A
#   rx_data[0:3] = lower 3 bits of second byte = 0x3D & 0x07 = 0b101 = 5
#   Combined: 0x3A in bits [10:3], 0b101 in bits [2:0]
#   But endpoint is 4 bits, so we need bit 3 as well!

# Actually, the code extracts rx_data[0:3] which is 3 bits, but endpoint is 4 bits.
# This is a BUG in the code! It should extract rx_data[0:4] for the full endpoint.

# Let me verify this is the issue:
print("\n\n=== ROOT CAUSE ANALYSIS ===")
print("\nThe code extracts endpoint bits as rx_data[0:3] (3 bits)")
print("But USB endpoint is 4 bits!")
print("\nFor byte 0x3D = 0b00111101:")
print(f"  rx_data[0:3] = 0b{0x3D & 0x07:03b} = {0x3D & 0x07} (3 bits)")
print(f"  rx_data[0:4] = 0b{0x3D & 0x0F:04b} = {0x3D & 0x0F} (4 bits, CORRECT)")

# The test expects endpoint 0xA = 10 = 0b1010
# But the code extracts only 3 bits: 0b101 = 5
# So the CRC is computed with wrong endpoint value!

# Let me compute what CRC the code would calculate:
addr_from_test = 0x3A
ep_3bits = 0x3D & 0x07  # 5
ep_4bits = 0x3D & 0x0F  # 13 (0xD)

token_for_3bit_ep = (addr_from_test << 3) | ep_3bits
crc_for_3bit_ep = compute_crc5_from_token(token_for_3bit_ep)
print(f"\nCRC5 computed with 3-bit endpoint (value={ep_3bits}):")
print(f"  Token = 0b{token_for_3bit_ep:011b}")
print(f"  CRC5 = 0b{crc_for_3bit_ep:05b} = {crc_for_3bit_ep}")

token_for_4bit_ep = (addr_from_test << 4) | ep_4bits
crc_for_4bit_ep = compute_crc5_from_token(token_for_4bit_ep)
print(f"\nCRC5 computed with 4-bit endpoint (value={ep_4bits}):")
print(f"  Token = 0b{token_for_4bit_ep:011b}")
print(f"  CRC5 = 0b{crc_for_4bit_ep:05b} = {crc_for_4bit_ep}")

# Extracted CRC from test data:
extracted_crc = (0x3D >> 3) & 0x1F  # bits [7:3] = 0b110 = 6
print(f"\nExtracted CRC5 from test data byte 0x3D: 0b{extracted_crc:05b} = {extracted_crc}")

print("\n=== CONCLUSION ===")
if crc_for_3bit_ep == extracted_crc:
    print("The CRC5 matches when using 3-bit endpoint extraction!")
    print("But the test expects endpoint 0xA, and the data has endpoint 0xD")
    print("\nThere are TWO issues:")
    print("  1. The code extracts only 3 bits for endpoint instead of 4")
    print("  2. The test data has endpoint 0xD, not 0xA as claimed")
elif crc_for_4bit_ep == extracted_crc:
    print("The CRC5 matches when using 4-bit endpoint extraction!")
    print("But the code uses 3-bit extraction, causing the mismatch")
    print("\nThe BUG is in the code: it extracts rx_data[0:3] instead of rx_data[0:4]")
else:
    print("Neither 3-bit nor 4-bit endpoint CRC matches!")
    print("The test data might be completely wrong.")
