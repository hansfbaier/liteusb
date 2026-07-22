#!/usr/bin/env python3
import re
with open('doc/architecture.html') as f: html = f.read()
html = re.sub(r'\n*<script type="WaveDrom">.*?</script>\n*', '\n', html, flags=re.DOTALL)
with open('doc/architecture.html', 'w') as f: f.write(html)
print('Stripped all WaveDrom scripts')
