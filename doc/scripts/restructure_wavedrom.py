#!/usr/bin/env python3
"""Restructure architecture.html — place each WaveDrom script inline at its module section."""
import json, os, re

# Map diagram name → (heading text to place after, match type)
PLACEMENT = {
    'ulpi_transmit':     ('ULPITransmitTranslator — Test WaveDrom', 'h4'),
    'token_detect':      ('3.1 USBTokenDetector', 'h3'),
    'handshake_detect':  ('3.2 USBHandshakeDetector', 'h3'),
    'data_rx':           ('3.4 USBDataPacketReceiver', 'h3'),
    'data_deserialize':  ('3.5 USBDataPacketDeserializer', 'h3'),
    'data_gen':          ('3.6 USBDataPacketGenerator', 'h3'),
    'handshake_gen':     ('3.7 USBHandshakeGenerator', 'h3'),
    'reset_seq':         ('4.1 USBResetSequencer', 'h3'),
    'control_ep0':       ('5.3 USBControlEndpoint (EP0)', 'h3'),
    'transfer_in':       ('6.1 USBInTransferManager', 'h3'),
    'descriptor':        ('7.1 USBDescriptorStreamGenerator', 'h3'),
    'setup_decoder':     ('8.2 USBSetupDecoder', 'h3'),
    'stream_boundary':   ('9.2 USBOutStreamBoundaryDetector', 'h3'),
}

# Read JSONs
diagrams = {}
for name in PLACEMENT:
    with open(f'doc/wavedrom/{name}.json') as f:
        diagrams[name] = json.load(f)

# Read HTML
with open('doc/architecture.html') as f:
    html = f.read()

# Remove existing placeholder divs (e.g. <div id="token_detect" class="wavedrom"></div>)
for name in PLACEMENT:
    html = re.sub(rf'<div id="{name}" class="wavedrom"></div>\n?', '', html)

# Remove the old script block at the bottom (13 inline scripts + the ProcessAll call)
old_block = html.rfind('<script type="WaveDrom">')
if old_block != -1:
    end_block = html.index('</body>', old_block)
    # Find the start of the first WaveDrom script
    start_block = html[:old_block].rfind('<h4>')
    html = html[:start_block] + '\n</body>\n</html>'

# Now insert each diagram after its target heading
for name, (target_text, tag_type) in PLACEMENT.items():
    json_str = json.dumps(diagrams[name])
    title = diagrams[name]['head']['text']
    # Find the heading containing target_text
    pattern = re.escape(target_text)
    match = re.search(rf'<{tag_type}[^>]*>.*{pattern}.*</{tag_type}>', html)
    if match:
        insert_pos = match.end()
        block = f'\n\n<script type="WaveDrom">{json_str}</script>\n'
        html = html[:insert_pos] + block + html[insert_pos:]
        print(f'  Placed {name} after "{target_text[:40]}..."')
    else:
        print(f'  NOT FOUND: {name} → "{target_text}"')

# Ensure ProcessAll runs at the end
html = html.replace('</body>', '<script>window.onload=function(){WaveDrom.ProcessAll()}</script>\n</body>')

with open('doc/architecture.html', 'w') as f:
    f.write(html)

print(f'\nDone: {len(PLACEMENT)} diagrams placed inline')
