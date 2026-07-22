#!/usr/bin/env python3
"""Inline WaveDrom JSON into architecture.html."""
import json, os

scripts = []
names = ['ulpi_transmit','token_detect','handshake_detect','data_rx',
         'data_deserialize','data_gen','handshake_gen','reset_seq',
         'control_ep0','transfer_in','descriptor','setup_decoder',
         'stream_boundary']

for name in names:
    with open(f'doc/wavedrom/{name}.json') as f:
        data = json.load(f)
    json_str = json.dumps(data)
    scripts.append(f'<h4>{data["head"]["text"]}</h4>')
    scripts.append(f'<script type="WaveDrom">{json_str}</script>')

with open('doc/architecture.html') as f:
    html = f.read()

old_start = "<script>\n// WaveDrom"
old_end = '</script>'
start_idx = html.index(old_start)
end_idx = html.index(old_end, start_idx) + len(old_end)

new = '\n'.join(scripts) + '\n<script>window.onload=function(){WaveDrom.ProcessAll()}</script>'
html = html[:start_idx] + new + html[end_idx:]

with open('doc/architecture.html', 'w') as f:
    f.write(html)

print(f'Updated: {len(html)} bytes, {len(names)} diagrams inlined')
