#!/usr/bin/env python3
"""Regenerate architecture.html from generated WaveDrom JSONs.

Usage:
    python3 doc/scripts/regenerate_docs.py

This removes all existing inline WaveDrom <script> blocks, then re-inserts
module-level diagrams after their section headings and per-test diagrams
after each corresponding <li> item.
"""
import json, re, os

HTML = 'doc/architecture.html'
WAVEDROM = 'doc/wavedrom'

with open(HTML) as f:
    html = f.read()

# 1. Remove every existing WaveDrom script block.
html = re.sub(r'<script type="WaveDrom">.*?</script>\n?', '', html, flags=re.DOTALL)

# 2. Remove the old ProcessAll script; we'll re-add it at the end.
html = re.sub(r'<script>window\.onload=function\(\)\{WaveDrom\.ProcessAll\(\)\}</script>\n?', '', html)

# 3. Insert module-level diagrams after their target headings.
MODULE_PLACEMENT = [
    ('token_detect',      '3.1 USBTokenDetector', 'h3'),
    ('handshake_detect',  '3.2 USBHandshakeDetector', 'h3'),
    ('data_rx',           '3.4 USBDataPacketReceiver', 'h3'),
    ('data_deserialize',  '3.5 USBDataPacketDeserializer', 'h3'),
    ('data_gen',          '3.6 USBDataPacketGenerator', 'h3'),
    ('handshake_gen',     '3.7 USBHandshakeGenerator', 'h3'),
    ('timer_resets_and_delays', '3.8 USBInterpacketTimer', 'h3'),
    ('reset_seq',         '4.1 USBResetSequencer', 'h3'),
    ('device_enumeration', '4.2 USBDevice / USB2Device', 'h3'),
    ('control_ep0',       '5.3 USBControlEndpoint (EP0)', 'h3'),
    ('transfer_in',       '6.1 USBInTransferManager', 'h3'),
    ('descriptor',        '7.1 USBDescriptorStreamGenerator', 'h3'),
    ('setup_decoder',     '8.2 USBSetupDecoder', 'h3'),
    ('stream_boundary',   '9.2 USBOutStreamBoundaryDetector', 'h3'),
    ('ulpi_transmit',     'ULPITransmitTranslator', 'h4'),
]

for name, target_text, tag in MODULE_PLACEMENT:
    path = f'{WAVEDROM}/{name}.json'
    if not os.path.exists(path):
        print(f'  skip module {name}: {path} missing')
        continue
    with open(path) as f:
        data = json.load(f)
    json_str = json.dumps(data)
    pattern = re.escape(target_text)
    match = re.search(rf'<{tag}[^>]*>.*{pattern}.*</{tag}>', html)
    if not match:
        print(f'  module heading not found: {target_text}')
        continue
    insert_pos = match.end()
    block = f'\n\n<script type="WaveDrom">{json_str}</script>\n'
    html = html[:insert_pos] + block + html[insert_pos:]
    print(f'  placed module {name}')

# 4. Insert per-test diagrams after each matching <li>.
PER_TEST_PLACEMENT = [
    ('token_detect_valid', 'test_valid_token</b>', 'token_detect'),
    ('token_detect_sof', 'test_valid_start_of_frame</b>', 'token_detect_sof'),
    ('token_detect_mismatch', 'test_token_to_other_device</b>', 'token_detect_mismatch'),
    ('data_rx', 'test_data_receive</b>', 'data_rx'),
    ('data_rx_zlp', 'test_zlp</b> (DUT: <code>USBDataPacketReceiver', 'data_rx_zlp'),
    ('data_deserialize', 'test_packet_rx</b>', 'data_deserialize'),
    ('data_deserialize_invalid', 'test_invalid_rx</b>', 'data_deserialize_invalid'),
    ('data_gen', 'test_simple_data_generation</b>', 'data_gen'),
    ('data_gen_single', 'test_single_byte</b>', 'data_gen_single'),
    ('data_gen_zlp', 'test_zlp_generation</b> (DUT: <code>USBDataPacketGenerator', 'data_gen_zlp'),
    ('handshake_gen', 'test_ack_generation</b>', 'handshake_gen'),
    ('handshake_gen_ready', 'test_already_ready</b>', 'handshake_gen_ready'),
    ('handshake_nak', 'test_nak</b>', 'handshake_nak'),
    ('handshake_stall', 'test_stall</b>', 'handshake_stall'),
    ('transfer_in', 'test_normal_transfer</b>', 'transfer_in'),
    ('transfer_nak_retransmit', 'test_normal_transfer</b>', 'transfer_nak_retransmit'),
    ('transfer_nak_not_ready', 'test_nak_when_not_ready</b>', 'transfer_nak_not_ready'),
    ('transfer_zlp_behavior', 'test_zlp_generation</b> (DUT: <code>USBInTransferManager', 'transfer_zlp_behavior'),
    ('transfer_discard', 'test_discard</b>', 'transfer_discard'),
    ('request_valid_sequence', 'test_valid_sequence_receive</b>', 'request_valid_sequence'),
    ('request_fs_delay', 'test_fs_interpacket_delay</b>', 'request_fs_delay'),
    ('request_truncated', 'test_short_setup_packet</b>', 'request_truncated'),
    ('stream_boundary', 'test_boundary_detection</b>', 'stream_boundary'),
    ('ulpi_simple_transmit', 'test_simple_transmit</b>', 'ulpi_simple_transmit'),
    ('ulpi_handshake', 'test_handshake</b>', 'ulpi_handshake'),
    ('ulpi_idle_behavior', 'test_idle_behavior</b>', 'ulpi_idle_behavior'),
    ('ulpi_register_read', 'test_register_read</b>', 'ulpi_register_read'),
    ('ulpi_interrupted_read', 'test_interrupted_read</b>', 'ulpi_interrupted_read'),
    ('ulpi_register_write', 'test_register_write</b>', 'ulpi_register_write'),
    ('ulpi_decode', 'test_decode</b>', 'ulpi_decode'),
    ('ulpi_multiwrite', 'test_multiwrite_behavior</b>', 'ulpi_multiwrite'),
    ('device_long_descriptor', 'test_long_descriptor</b>', 'device_long_descriptor'),
    ('device_descriptor_zlp', 'test_descriptor_zlp</b>', 'device_descriptor_zlp'),
]

for placement_name, pattern, json_name in PER_TEST_PLACEMENT:
    path = f'{WAVEDROM}/{json_name}.json'
    if not os.path.exists(path):
        print(f'  skip per-test {placement_name}: {path} missing')
        continue
    idx = html.find(pattern)
    if idx == -1:
        print(f'  per-test pattern not found: {pattern}')
        continue
    li_end = html.find('</li>', idx)
    if li_end == -1:
        print(f'  no </li> after {pattern}')
        continue
    with open(path) as f:
        data = json.load(f)
    json_str = json.dumps(data)
    insert_pos = li_end + len('</li>')
    block = f'\n<script type="WaveDrom">{json_str}</script>\n'
    html = html[:insert_pos] + block + html[insert_pos:]
    print(f'  placed per-test {placement_name}')

# 5. Ensure ProcessAll is at the end.
html = html.replace('</body>', '\n<script>window.onload=function(){WaveDrom.ProcessAll()}</script>\n</body>')

with open(HTML, 'w') as f:
    f.write(html)

print(f'\nRegenerated {HTML}')
