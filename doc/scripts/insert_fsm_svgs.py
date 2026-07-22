#!/usr/bin/env python3
"""Insert FSM SVG diagram references into architecture.html."""

html = open('doc/architecture.html').read()

# Each entry: (unique context substring near the FSM, svg path)
fsm_entries = [
    ('READ_PID → READ_TOKEN', 'fsm/token_detector.svg'),
    ('AWAIT_COMPLETION',      'fsm/handshake_detector.svg'),
    ('WAIT_FOR_FIRST_DATA',   'fsm/data_packet_receiver.svg'),
    ('SEND_CRC',              'fsm/data_packet_generator.svg'),
    ('TRANSMIT',              'fsm/handshake_generator.svg'),
    ('INITIALIZE →',          'fsm/reset_sequencer.svg'),
    ('DATA_IN → DATA_OUT',    'fsm/control_endpoint.svg'),
    ('WAIT_FOR_DATA →',       'fsm/transfer_in.svg'),
    ('STREAMING → DONE',      'fsm/descriptor_generator.svg'),
    ('WAIT_FOR_FIRST',        'fsm/stream_boundary.svg'),
]

for context_str, svg in fsm_entries:
    # Find the context string
    idx = html.find(context_str)
    if idx < 0:
        print(f'  SKIP {svg}: context "{context_str}" not found')
        continue
    # Find the end of the containing <div> block
    div_start = html.rfind('<div class="fsm">', 0, idx)
    div_end = html.find('</div>', idx)
    if div_end < 0:
        div_end = html.find('\n\n', idx + len(context_str))
    insert_pos = div_end if div_end > 0 else idx + len(context_str)
    img_tag = f'\n<img src="{svg}" alt="FSM" style="max-width:100%;margin:8px 0 16px 0;">\n'
    html = html[:insert_pos] + img_tag + html[insert_pos:]
    print(f'  OK {svg} at offset {insert_pos}')

open('doc/architecture.html', 'w').write(html)
print('Done')
