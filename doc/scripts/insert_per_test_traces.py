#!/usr/bin/env python3
"""Insert per-test-method WaveDrom diagrams after each test <li> in section 10."""

import json, os, re

with open('doc/architecture.html') as f:
    html = f.read()

# Load all per-test JSONs
TESTS = {}
for fname in os.listdir('doc/wavedrom'):
    if fname.endswith('.json'):
        with open(f'doc/wavedrom/{fname}') as f:
            TESTS[fname.replace('.json', '')] = json.load(f)

# Mapping: pattern in <li> → diagram name
# We match the <li> text and insert the <script> after the </li>
# Match now uses DUT-annotated <li> text
PLACEMENT = [
    ('test_valid_start_of_frame </b> (DUT', 'token_detect_sof'),
    ('test_token_to_other_device </b> (DUT', 'token_detect_mismatch'),
    ('test_zlp </b> (DUT: <code>USBDataPacketReceiver', 'data_rx_zlp'),
    ('test_invalid_rx </b> (DUT', 'data_deserialize_invalid'),
    ('test_single_byte </b> (DUT', 'data_gen_single'),
    ('test_zlp_generation </b> (DUT: <code>USBDataPacketGenerator', 'data_gen_zlp'),
    ('test_normal_transfer </b> (DUT', 'transfer_nak_retransmit'),
    ('test_zlp_generation </b> (DUT: <code>USBInTransferManager', 'transfer_zlp_behavior'),
    ('test_discard </b> (DUT', 'transfer_discard'),
    ('test_fs_interpacket_delay </b> (DUT', 'request_fs_delay'),
    ('test_short_setup_packet </b> (DUT', 'request_truncated'),
    ('test_full_speed_reset </b> (DUT', 'reset_se0_timing'),
    ('test_enumeration </b> (DUT', 'device_enumeration'),
    ('test_usb2_loopback </b> (DUT', 'integration_loopback'),
    ('test_usb2_stress </b> (DUT', 'integration_stress'),
]

count = 0
for pattern, name in PLACEMENT:
    if name not in TESTS:
        print(f'  MISSING JSON: {name}')
        continue

    json_str = json.dumps(TESTS[name])
    title = TESTS[name]['head']['text']

    # Find the <li> containing this pattern
    escaped = re.escape(pattern)
    match = re.search(rf'<li>{escaped}.*?</li>', html, re.DOTALL)
    if not match:
        print(f'  NOT FOUND: {name} → pattern not in HTML')
        continue
    # match is guaranteed non-None here
    insert_pos = match.end()
    block = f'\n<script type="WaveDrom">{json_str}</script>\n'
    html = html[:insert_pos] + block + html[insert_pos:]
    count += 1
    print(f'  Placed {name} after "{pattern[:50]}..."')


with open('doc/architecture.html', 'w') as f:
    f.write(html)

print(f'\nDone: {count} per-test diagrams placed')
