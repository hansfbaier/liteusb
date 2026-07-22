#!/usr/bin/env python3
html = open('doc/architecture.html').read()
tests = ['test_valid_token','test_data_receive','test_simple_data_generation',
'test_already_ready','test_ack_generation','test_resets_and_delays',
'test_single_packet_in','test_single_packet_out','test_unavailable_descriptor',
'test_unavailable_index_type','test_nak_when_not_ready',
'test_valid_sequence_receive','test_long_descriptor']
for t in tests:
    idx = html.find(t+'</b>')
    if idx==-1: print(f'{t}: NOT FOUND'); continue
    li_end = html.find('</li>', idx)
    has = 'WaveDrom' in html[li_end:li_end+300]
    print(f'{t}: {"HAS trace" if has else "MISSING"}')
