#!/usr/bin/env python3
"""Move all WaveDrom diagrams from sections 2-9 to their test descriptions in section 10."""

import json, os, re

# Map diagram name → heading text in section 10 where it belongs
PLACEMENT = {
    'token_detect':        'Token Detection Tests',
    'handshake_detect':    'Handshake Detection Tests',
    'data_rx':             'Data Packet Receiver Tests',
    'data_deserialize':    'Data Packet Deserializer Tests',
    'data_gen':            'Data Packet Generator Tests',
    'handshake_gen':       'Handshake Generator Tests',
    'reset_seq':           '10.6 Reset Tests',
    'descriptor':          '10.3 Descriptor Tests',
    'transfer_in':         '10.4 Transfer Tests',
    'setup_decoder':       '10.5 Request Tests',
    'stream_boundary':     '10.8 Stream Tests',
    'ulpi_transmit':       '10.7 ULPI Tests',
    'control_ep0':         '10.9 Device-Level Tests',
}

with open('doc/architecture.html') as f:
    html = f.read()

# 1. Remove all existing <script type="WaveDrom"> blocks
html = re.sub(r'\n*<script type="WaveDrom">.*?</script>\n*', '\n', html, flags=re.DOTALL)

# 2. Remove leftover empty <h4> lines that were paired with removed scripts
html = re.sub(r'\n<h4>[^<]+</h4>\n\n', '\n', html)

# 3. Load all diagrams
diagrams = {}
for name in PLACEMENT:
    with open(f'doc/wavedrom/{name}.json') as f:
        diagrams[name] = json.load(f)

# 4. Insert each diagram after its target heading
for name, target_text in PLACEMENT.items():
    json_str = json.dumps(diagrams[name])
    # Find the heading containing target_text
    pattern = re.escape(target_text)
    # Match <h3>...</h3> or <h4>...</h4>
    match = re.search(rf'<(h[34])[^>]*>.*{pattern}.*</\1>', html)
    if match:
        insert_pos = match.end()
        block = f'\n\n<script type="WaveDrom">{json_str}</script>\n'
        html = html[:insert_pos] + block + html[insert_pos:]
        print(f'  Placed {name} after "{target_text}"')
    else:
        print(f'  NOT FOUND: {name} → "{target_text}"')

with open('doc/architecture.html', 'w') as f:
    f.write(html)

print(f'\nDone: {len(PLACEMENT)} diagrams placed in section 10')
