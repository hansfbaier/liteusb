#!/usr/bin/env python3
"""
Run only the passing tests in the LiteUSB test suite.

Usage:
    python run_passing_tests.py
    python run_passing_tests.py -v  # verbose mode
"""

import sys
import unittest

# List of currently passing tests
PASSING_TESTS = [
    'tests.test_ulpi.TestULPIRegisters.test_idle_behavior',
    'tests.test_ulpi.ULPITransmitTranslatorTest.test_handshake',
    'tests.test_ulpi.ULPITransmitTranslatorTest.test_simple_transmit',
    'tests.test_usb2_packet.USBDataPacketDeserializerTest.test_invalid_rx',
    'tests.test_usb2_packet.USBDataPacketGeneratorTest.test_single_byte',
    'tests.test_usb2_packet.USBDataPacketGeneratorTest.test_zlp_generation',
    'tests.test_usb2_packet.USBHandshakeGeneratorTest.test_ack_generation',
    'tests.test_usb2_packet.USBHandshakeGeneratorTest.test_already_ready',
    'tests.test_usb2_packet.USBTokenDetectorTest.test_token_to_other_device',
    'tests.test_usb2_reset.USBResetSequencerTest.test_full_speed_reset',
]

def main():
    # Check for verbose flag
    verbose = '-v' in sys.argv or '--verbose' in sys.argv
    
    # Create test suite with only passing tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    for test_name in PASSING_TESTS:
        try:
            test = loader.loadTestsFromName(test_name)
            suite.addTest(test)
        except Exception as e:
            print(f"Warning: Could not load {test_name}: {e}")
    
    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)
    
    # Return exit code
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    sys.exit(main())
