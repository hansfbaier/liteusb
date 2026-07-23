#!/usr/bin/env python3
"""Generate WaveDrom diagrams from unit-test VCDs and regenerate architecture.html.

Usage:
    python3 doc/scripts/generate_docs.py

This is the one-stop script for the documentation diagram pipeline:
1. Runs the test suite with GENERATE_VCDS=1 to produce doc/vcd/*.vcd.
2. Converts the VCD traces into doc/wavedrom/*.json.
3. Rebuilds doc/architecture.html with the generated diagrams.
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run(cmd):
    print(f"\n$ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)

if __name__ == "__main__":
    run("GENERATE_VCDS=1 python3 -m pytest tests/ -q")
    run("python3 doc/scripts/generate_wavedrom.py")
    run("python3 doc/scripts/regenerate_docs.py")
    print("\nDocumentation diagrams regenerated.")
