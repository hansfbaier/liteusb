#!/usr/bin/env python3
"""Simpler approach: find <li> by pattern via html.find, then insert after </li>."""

import json, os

with open('doc/architecture.html') as f:
    html = f.read()

# Load JSONs
TESTS = {}
for fname in os.listdir('doc/wavedrom'):
    if fname.endswith('.json'):
        with open(f'doc/wavedrom/{fname}') as f:
            TESTS[fname.replace('.json', '')] = json.load(f)

# pattern_fragment → diagram_name
PLACEMENT = [
    ('test_valid_token</b>', 'token_detect_valid'),
    ('test_valid_start_of_frame</b>', 'token_detect_sof'),
    ('test_token_to_other_device</b>', 'token_detect_mismatch'),
    ('test_zlp</b>', 'data_rx_zlp'),
    ('test_packet_rx</b>', 'data_deserialize_packet_rx'),
    ('test_invalid_rx</b>', 'data_deserialize_invalid'),
    ('test_simple_data_generation</b>', 'data_gen_simple'),
    ('test_single_byte</b>', 'data_gen_single'),
    ('test_zlp_generation</b> (DUT: <code>USBDataPacketGenerator', 'data_gen_zlp'),
    ('test_normal_transfer</b>', 'transfer_nak_retransmit'),
    ('test_zlp_generation</b> (DUT: <code>USBInTransferManager', 'transfer_zlp_behavior'),
    ('test_discard</b>', 'transfer_discard'),
    ('test_nak_when_not_ready</b>', 'transfer_nak_not_ready'),
    ('test_already_ready</b>', 'handshake_gen_ready'),
    ('test_valid_sequence_receive</b>', 'request_valid_sequence'),
    ('test_fs_interpacket_delay</b>', 'request_fs_delay'),
    ('test_short_setup_packet</b>', 'request_truncated'),
    ('test_resets_and_delays</b>', 'timer_resets_and_delays'),
    ('test_full_speed_reset</b>', 'reset_se0_timing'),
    # ULPI tests (8 tests, all 8 now have per-test diagrams)
    ('test_idle_behavior</b>', 'ulpi_idle_behavior'),
    ('test_register_read</b>', 'ulpi_register_read'),
    ('test_interrupted_read</b>', 'ulpi_interrupted_read'),
    ('test_register_write</b>', 'ulpi_register_write'),
    ('test_decode</b>', 'ulpi_decode'),
    ('test_multiwrite_behavior</b>', 'ulpi_multiwrite'),
    ('test_simple_transmit</b>', 'ulpi_simple_transmit'),
    ('test_handshake</b>', 'ulpi_handshake'),
    ('test_enumeration</b>', 'device_enumeration'),
    ('test_long_descriptor</b>', 'device_long_descriptor'),
    ('test_usb2_loopback</b>', 'integration_loopback'),
    ('test_usb2_stress</b>', 'integration_stress'),
]

count = 0
for pattern, name in PLACEMENT:
    idx = html.find(pattern)
    if idx == -1:
        print(f'  NOT FOUND: {name}')
        continue

    # Find closing </li> after pattern
    li_end = html.find('</li>', idx)
    if li_end == -1:
        print(f'  NO </li> after {name}')
        continue

    json_str = json.dumps(TESTS[name])
    insert_pos = li_end + len('</li>')
    block = f'\n<script type="WaveDrom">{json_str}</script>\n'
    html = html[:insert_pos] + block + html[insert_pos:]
    count += 1
    print(f'  Placed {name}')

# Also add handshake_nak after the general handshake list item
idx = html.find('DUT: <code>USBHandshakeDetector</code>.')
if idx != -1:
    li_end = html.find('</li>', idx)
    if li_end != -1:
        json_str = json.dumps(TESTS['handshake_nak'])
        html = html[:li_end+5] + f'\n<script type="WaveDrom">{json_str}</script>\n' + html[li_end+5:]
        count += 1
        print('  Placed handshake_nak')

# handshake_stall
idx = html.find('DUT: <code>USBHandshakeDetector</code>.', li_end+100) if 'li_end' in dir() else -1
if idx == -1:
    idx = html.rfind('DUT: <code>USBHandshakeDetector</code>.')
if idx != -1:
    li_end = html.find('</li>', idx)
    if li_end != -1:
        json_str = json.dumps(TESTS['handshake_stall'])
        html = html[:li_end+5] + f'\n<script type="WaveDrom">{json_str}</script>\n' + html[li_end+5:]
        count += 1
        print('  Placed handshake_stall')

with open('doc/architecture.html', 'w') as f:
    f.write(html)

print(f'\nDone: {count} per-test diagrams placed')
