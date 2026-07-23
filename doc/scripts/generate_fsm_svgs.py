#!/usr/bin/env python3
"""Generate FSM state diagram SVGs by parsing actual source code transitions.

Usage:
    python3 liteusb/doc/scripts/generate_fsm_svgs.py              # all FSMs
    python3 liteusb/doc/scripts/generate_fsm_svgs.py token_detector control_endpoint
    python3 liteusb/doc/scripts/generate_fsm_svgs.py --list       # list available keys

Automatically locates the LiteX workspace root from its own path, so it can
be invoked from any working directory.
"""
import ast
import os
import sys

import graphviz

# Locate the workspace root relative to this script (3 levels up).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))

OUT = os.path.join(ROOT, "liteusb", "doc", "fsm")

SKIN = {
    "rankdir": "LR",
    "fontname": "Helvetica",
    "fontsize": "10",
    "nodesep": "0.4",
    "ranksep": "0.6",
}

# Map diagram key → (source_file_relative_to_workspace_root, class_name)
FSMS: dict[str, tuple[str, str]] = {
    "token_detector":       ("liteusb/liteusb/gateware/usb/usb2/packet.py",     "USBTokenDetector"),
    "handshake_detector":   ("liteusb/liteusb/gateware/usb/usb2/packet.py",     "USBHandshakeDetector"),
    "data_packet_receiver": ("liteusb/liteusb/gateware/usb/usb2/packet.py",     "USBDataPacketReceiver"),
    "data_packet_generator":("liteusb/liteusb/gateware/usb/usb2/packet.py",     "USBDataPacketGenerator"),
    "handshake_generator":  ("liteusb/liteusb/gateware/usb/usb2/packet.py",     "USBHandshakeGenerator"),
    "reset_sequencer":      ("liteusb/liteusb/gateware/usb/usb2/reset.py",      "USBResetSequencer"),
    "control_endpoint":     ("liteusb/liteusb/gateware/usb/usb2/control.py",    "USBControlEndpoint"),
    "transfer_in":          ("liteusb/liteusb/gateware/usb/usb2/transfer.py",   "USBInTransferManager"),
    "descriptor_generator": ("liteusb/liteusb/gateware/usb/usb2/descriptor.py", "USBDescriptorStreamGenerator"),
    "stream_boundary":      ("liteusb/liteusb/gateware/usb/stream.py",          "USBOutStreamBoundaryDetector"),
}


def _find_class(tree: ast.AST, class_name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _extract_string_arg(call: ast.Call, index: int = 0) -> str | None:
    """Return the string value of a positional argument at *index*, or None."""
    if len(call.args) > index:
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _walk_nextstate_strings(node: ast.AST) -> list[str]:
    """Recursively find all NextState("X") string arguments within *node*."""
    results: list[str] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "NextState"
        ):
            target = _extract_string_arg(child, 0)
            if target:
                results.append(target)
    return results


def extract_fsm_graph(filepath: str, class_name: str) -> dict:
    """Parse *filepath*, find *class_name*, extract its FSM graph.

    Returns a dict with:
      - "edges": list of (source, target) tuples
      - "states": set of all state names
      - "reset": reset state name (defaults to "IDLE" per migen convention)
      - "title": class name for the diagram title
    """
    with open(filepath, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    cls = _find_class(tree, class_name)
    if cls is None:
        raise ValueError(f"Class {class_name} not found in {filepath}")

    # --- detect reset_state from FSM(reset_state="...") ---
    reset_state = "IDLE"  # migen default
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "FSM"
        ):
            for kw in node.keywords:
                if kw.arg == "reset_state" and isinstance(kw.value, ast.Constant):
                    reset_state = kw.value.value
                    break

    # --- walk all fsm.act("STATE", ...) blocks ---
    edges: set[tuple[str, str]] = set()
    all_states: set[str] = set()

    for node in ast.walk(cls):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "act"):
            continue

        source = _extract_string_arg(node, 0)
        if source is None:
            continue

        all_states.add(source)

        for target in _walk_nextstate_strings(node):
            all_states.add(target)
            edges.add((source, target))

    return {
        "edges": sorted(edges),
        "states": sorted(all_states),
        "reset": reset_state,
        "title": class_name,
    }


def build_diagram(key: str, graph: dict, out_dir: str) -> None:
    """Render a graphviz SVG from the extracted FSM graph."""
    g = graphviz.Digraph(key, graph_attr=SKIN)
    g.attr(label=f"State Machine: {graph['title']}", labelloc="t", fontsize="12")

    states = graph["states"]
    reset = graph["reset"]
    edges = graph["edges"]

    for s in states:
        attrs = {"shape": "box", "style": "rounded,filled", "fillcolor": "#e8e8e8"}
        if s == reset:
            attrs["fillcolor"] = "#c0e0c0"
            attrs["peripheries"] = "2"
        if "DONE" in s or "COMPLETE" in s:
            attrs["fillcolor"] = "#e0c0c0"
        g.node(s, label=s.replace("_", " "), **attrs)

    for src, dst in edges:
        g.edge(src, dst)

    if "IDLE" in states:
        g.edge("IDLE", "IDLE", "rx_active=0", style="dashed", color="#888")

    g.render(filename=key, directory=out_dir, format="svg", cleanup=True)
    print(f"  {key}.svg  ({len(states)} states, {len(edges)} edges)")


def main() -> None:
    args = sys.argv[1:]

    if "--list" in args or "-l" in args:
        print("Available FSM diagrams:")
        for key in sorted(FSMS):
            _, cls = FSMS[key]
            print(f"  {key:26s}  {cls}")
        return

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    # If specific keys are given, generate only those.
    keys = args if args else list(FSMS)
    unknown = [k for k in keys if k not in FSMS]
    if unknown:
        print(f"Error: unknown FSM key(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Use --list to see available keys.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUT, exist_ok=True)

    for key in keys:
        rel_path, class_name = FSMS[key]
        filepath = os.path.join(ROOT, rel_path)
        graph = extract_fsm_graph(filepath, class_name)
        build_diagram(key, graph, OUT)

    print(f"\n{len(keys)} FSM diagram(s) generated in {OUT}/")


if __name__ == "__main__":
    main()
