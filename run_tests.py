#!/usr/bin/env python3
#
# This file is part of LiteUSB.
#
# Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2025 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""Test runner script for LiteUSB.

This script sets up the Python path correctly and runs all tests.
Usage:
    python run_tests.py                    # Run all tests
    python run_tests.py test_usb2_packet   # Run specific test file
    python run_tests.py -v                 # Run with verbose output
"""

import sys
import os
import argparse
import unittest


def setup_python_path():
    """Setup Python path for LiteUSB testing."""
    # Get the directory containing this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Get the parent directory (contains liteusb package)
    parent_dir = os.path.dirname(script_dir)
    
    # Add paths in order of priority
    paths_to_add = [
        # Parent directory so 'import liteusb' works
        parent_dir,
        # python-usb-protocol for usb_protocol imports
        os.path.join(parent_dir, 'python-usb-protocol'),
        # migen for migen imports
        os.path.join(parent_dir, 'migen'),
        # litex for litex imports
        os.path.join(parent_dir, 'litex'),
    ]
    
    # Add paths to the beginning of sys.path if they exist
    for path in paths_to_add:
        if os.path.exists(path) and path not in sys.path:
            sys.path.insert(0, path)


def discover_tests(test_pattern=None, start_dir=None):
    """Discover tests in the tests directory.
    
    Args:
        test_pattern: Optional pattern to match test files (e.g., 'test_usb2_packet')
        start_dir: Directory to start test discovery from
        
    Returns:
        unittest.TestSuite with discovered tests
    """
    if start_dir is None:
        start_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests')
    
    if test_pattern:
        # If a specific test file is requested
        if not test_pattern.startswith('test_'):
            test_pattern = f'test_{test_pattern}'
        if not test_pattern.endswith('.py'):
            pattern = f'{test_pattern}.py'
        else:
            pattern = test_pattern
        
        # Discover tests matching the pattern
        loader = unittest.TestLoader()
        suite = loader.discover(start_dir, pattern=pattern)
    else:
        # Discover all tests
        loader = unittest.TestLoader()
        suite = loader.discover(start_dir, pattern='test_*.py')
    
    return suite


def main():
    """Main entry point for the test runner."""
    parser = argparse.ArgumentParser(
        description='Run LiteUSB tests',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python run_tests.py                    # Run all tests
  python run_tests.py -v                 # Run all tests with verbose output
  python run_tests.py usb2_packet        # Run test_usb2_packet.py only
  python run_tests.py test_usb2_device   # Run test_usb2_device.py only
        '''
    )
    
    parser.add_argument(
        'test_pattern',
        nargs='?',
        help='Specific test file to run (e.g., "usb2_packet" or "test_usb2_packet")'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    
    parser.add_argument(
        '--failfast',
        action='store_true',
        help='Stop on first failure'
    )
    
    parser.add_argument(
        '--tb', '--traceback',
        dest='traceback',
        choices=['short', 'long', 'line', 'no'],
        default='long',
        help='Control traceback output style (default: long)'
    )
    
    args = parser.parse_args()
    
    # Setup Python path
    setup_python_path()
    
    # Discover tests
    suite = discover_tests(args.test_pattern)
    
    # Check if any tests were found
    if suite.countTestCases() == 0:
        print(f"No tests found matching pattern: {args.test_pattern or 'test_*.py'}")
        print(f"Searched in: {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests')}")
        sys.exit(1)
    
    # Configure test runner
    verbosity = 2 if args.verbose else 1
    
    # Run tests
    runner = unittest.TextTestRunner(
        verbosity=verbosity,
        failfast=args.failfast,
        tb_locals=(args.traceback == 'long')
    )
    
    result = runner.run(suite)
    
    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
