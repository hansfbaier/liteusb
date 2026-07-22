#!/usr/bin/env python3
"""Move DUT annotations from headings to individual test items in section 10."""

with open('doc/architecture.html') as f:
    html = f.read()

# 1. Strip DUT annotations from headings
import re
html = re.sub(r' \u2014 DUT: <code>[^<]+</code>(, <code>[^<]+</code>)*( \([^)]+\))?', '', html)

# 2. Add DUT annotations to each <li> item

# Token Detection Tests
html = html.replace(
    '<li><b>test_valid_token</b>: Sends an OUT token to address 0x3a',
    '<li><b>test_valid_token</b> (DUT: <code>USBTokenDetector</code>): Sends an OUT token to address 0x3a')
html = html.replace(
    '<li><b>test_valid_start_of_frame</b>: Sends SOF token',
    '<li><b>test_valid_start_of_frame</b> (DUT: <code>USBTokenDetector</code>): Sends SOF token')
html = html.replace(
    '<li><b>test_token_to_other_device</b>: Sends token to 0x3a',
    '<li><b>test_token_to_other_device</b> (DUT: <code>USBTokenDetector</code>): Sends token to 0x3a')

# Handshake Detection Tests
html = html.replace(
    '<li>Verifies detection of ACK (PID 0xD2), NAK (0x5A), STALL (0x1E), NYET (0x96)',
    '<li>DUT: <code>USBHandshakeDetector</code>. Verifies detection of ACK (PID 0xD2), NAK (0x5A), STALL (0x1E), NYET (0x96)')

# Data Packet Receiver Tests
html = html.replace(
    '<li><b>test_data_receive</b>: Sends DATA0 + 8 data bytes',
    '<li><b>test_data_receive</b> (DUT: <code>USBDataPacketReceiver</code>, <code>USBDataPacketCRC</code>): Sends DATA0 + 8 data bytes')
html = html.replace(
    '<li><b>test_zlp</b>: Sends DATA1 + CRC only',
    '<li><b>test_zlp</b> (DUT: <code>USBDataPacketReceiver</code>, <code>USBDataPacketCRC</code>): Sends DATA1 + CRC only')

# Data Packet Deserializer Tests
html = html.replace(
    '<li><b>test_packet_rx</b>: Sends PID + 4 bytes',
    '<li><b>test_packet_rx</b> (DUT: <code>USBDataPacketDeserializer</code>; uses <code>USBDataPacketReceiver</code>, <code>USBDataPacketCRC</code>): Sends PID + 4 bytes')
html = html.replace(
    '<li><b>test_invalid_rx</b>: Sends corrupted CRC',
    '<li><b>test_invalid_rx</b> (DUT: <code>USBDataPacketDeserializer</code>; uses <code>USBDataPacketReceiver</code>, <code>USBDataPacketCRC</code>): Sends corrupted CRC')

# Data Packet Generator Tests
html = html.replace(
    '<li><b>test_simple_data_generation</b>: Feeds a stream with 8-byte payload',
    '<li><b>test_simple_data_generation</b> (DUT: <code>USBDataPacketGenerator</code>; uses <code>USBDataPacketCRC</code>): Feeds a stream with 8-byte payload')
html = html.replace(
    '<li><b>test_single_byte</b>: Single-byte stream',
    '<li><b>test_single_byte</b> (DUT: <code>USBDataPacketGenerator</code>; uses <code>USBDataPacketCRC</code>): Single-byte stream')
html = html.replace(
    '<li><b>test_zlp_generation</b>: ZLP request',
    '<li><b>test_zlp_generation</b> (DUT: <code>USBDataPacketGenerator</code>; uses <code>USBDataPacketCRC</code>): ZLP request')

# Handshake Generator Tests
html = html.replace(
    '<li><b>test_ack_generation</b>: Pulses issue_ack',
    '<li><b>test_ack_generation</b> (DUT: <code>USBHandshakeGenerator</code>): Pulses issue_ack')
html = html.replace(
    '<li><b>test_already_ready</b>: Same with tx.ready=1',
    '<li><b>test_already_ready</b> (DUT: <code>USBHandshakeGenerator</code>): Same with tx.ready=1')

# Interpacket Timer Tests
html = html.replace(
    '<li><b>test_resets_and_delays</b>: Configures FS speed',
    '<li><b>test_resets_and_delays</b> (DUT: <code>USBInterpacketTimer</code>): Configures FS speed')

# Endpoint Tests
html = html.replace(
    '<li><b>test_single_packet_in</b>: ISO IN endpoint',
    '<li><b>test_single_packet_in</b> (DUT: <code>USBIsochronousStreamInEndpoint</code>): ISO IN endpoint')
html = html.replace(
    '<li><b>test_single_packet_out</b>: ISO OUT endpoint',
    '<li><b>test_single_packet_out</b> (DUT: <code>USBIsochronousStreamOutEndpoint</code>; uses <code>TransactionalizedFIFO</code>): ISO OUT endpoint')

# Descriptor Tests - generic list items
html = html.replace(
    'All descriptor types (device, config, interface, endpoint, string, HID) tested',
    'DUT: <code>GetDescriptorHandlerBlock</code> (uses <code>USBDescriptorStreamGenerator</code>). All descriptor types (device, config, interface, endpoint, string, HID) tested')

# Transfer Tests
html = html.replace(
    '<li><b>test_normal_transfer</b>: Double-buffering',
    '<li><b>test_normal_transfer</b> (DUT: <code>USBInTransferManager</code>): Double-buffering')
html = html.replace(
    '<li><b>test_nak_when_not_ready</b>: NAK pulses',
    '<li><b>test_nak_when_not_ready</b> (DUT: <code>USBInTransferManager</code>): NAK pulses')
html = html.replace(
    '<li><b>test_zlp_generation</b>: ZLP after full last-packet',
    '<li><b>test_zlp_generation</b> (DUT: <code>USBInTransferManager</code>): ZLP after full last-packet')
html = html.replace(
    '<li><b>test_discard</b>: Discard drops queued packet',
    '<li><b>test_discard</b> (DUT: <code>USBInTransferManager</code>): Discard drops queued packet')

# Request Tests
html = html.replace(
    '<li><b>test_valid_sequence_receive</b>: Full SETUP transaction',
    '<li><b>test_valid_sequence_receive</b> (DUT: <code>USBSetupDecoder</code>; uses <code>USBDataPacketDeserializer</code>, <code>USBHandshakeGenerator</code>): Full SETUP transaction')
html = html.replace(
    '<li><b>test_fs_interpacket_delay</b>: 10-cycle gap before ACK',
    '<li><b>test_fs_interpacket_delay</b> (DUT: <code>USBSetupDecoder</code>; uses <code>USBDataPacketDeserializer</code>, <code>USBHandshakeGenerator</code>): 10-cycle gap before ACK')
html = html.replace(
    '<li><b>test_short_setup_packet</b>: Truncated (4-byte) setup',
    '<li><b>test_short_setup_packet</b> (DUT: <code>USBSetupDecoder</code>; uses <code>USBDataPacketDeserializer</code>): Truncated (4-byte) setup')

# Reset Tests
html = html.replace(
    '<li><b>test_full_speed_reset</b>: SE0',
    '<li><b>test_full_speed_reset</b> (DUT: <code>USBResetSequencer</code>): SE0')

# ULPI Tests - generic
html = html.replace(
    'Register read/write protocol verification',
    'DUT: <code>ULPIRegisterWindow</code>. Register read/write protocol verification')
html = html.replace(
    'DIR interruption handling',
    'DUT: <code>ULPIRegisterWindow</code>. DIR interruption handling')
html = html.replace(
    'Control translator multi-register writes',
    'DUT: <code>ULPIControlTranslator</code>. Control translator multi-register writes')
html = html.replace(
    'Transmit translator: SOF and ACK packet generation',
    'DUT: <code>ULPITransmitTranslator</code>. Transmit translator: SOF and ACK packet generation')

# Stream Tests
html = html.replace(
    '<li><b>test_boundary_detection</b>: 4-byte stream',
    '<li><b>test_boundary_detection</b> (DUT: <code>USBOutStreamBoundaryDetector</code>): 4-byte stream')

# Device-Level Tests
html = html.replace(
    '<li><b>test_enumeration</b>: Full USB enumeration sequence',
    '<li><b>test_enumeration</b> (DUT: <code>USBDevice</code>; exercises <code>USBControlEndpoint</code>, <code>StandardRequestHandler</code>, <code>GetDescriptorHandlerBlock</code>, <code>USBSetupDecoder</code>): Full USB enumeration sequence')
html = html.replace(
    '<li><b>test_long_descriptor</b>: Configuration descriptor with 30 endpoints',
    '<li><b>test_long_descriptor</b> (DUT: <code>USBDevice</code>; exercises <code>GetDescriptorHandlerBlock</code>): Configuration descriptor with 30 endpoints')

# Integration Tests
html = html.replace(
    '<li><b>test_usb2_loopback</b>: Full-device OUT',
    '<li><b>test_usb2_loopback</b> (DUT: <code>USBDevice</code> with <code>USBStreamOutEndpoint</code>; exercises <code>USBInTransferManager</code>): Full-device OUT')
html = html.replace(
    '<li><b>test_usb2_stress</b>: Full-device IN constant stream',
    '<li><b>test_usb2_stress</b> (DUT: <code>USBDevice</code> with <code>USBStreamOutEndpoint</code>; exercises <code>USBInTransferManager</code>): Full-device IN constant stream')

with open('doc/architecture.html', 'w') as f:
    f.write(html)

print('DUT annotations moved to individual test items')
