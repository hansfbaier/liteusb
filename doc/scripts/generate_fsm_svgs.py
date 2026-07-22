#!/usr/bin/env python3
"""Generate FSM state diagram SVGs for liteusb cores using graphviz."""
import graphviz

OUT = "doc/fsm"
SKIN = {
    "rankdir": "LR", "fontname": "Helvetica", "fontsize": "10",
    "nodesep": "0.4", "ranksep": "0.6",
}

FSMS = {
    "token_detector": {
        "title": "USBTokenDetector",
        "states": ["IDLE", "READ_PID", "READ_TOKEN_0", "READ_TOKEN_1", "TOKEN_COMPLETE"],
        "reset": "IDLE",
    },
    "handshake_detector": {
        "title": "USBHandshakeDetector",
        "states": ["IDLE", "READ_PID", "AWAIT_COMPLETION"],
        "reset": "IDLE",
    },
    "data_packet_receiver": {
        "title": "USBDataPacketReceiver",
        "states": ["IDLE", "READ_PID", "WAIT_FOR_FIRST_DATA", "RECEIVE_DATA", "CHECK_CRC"],
        "reset": "IDLE",
    },
    "data_packet_generator": {
        "title": "USBDataPacketGenerator",
        "states": ["IDLE", "SEND_PID", "SEND_PAYLOAD", "SEND_CRC0", "SEND_CRC1"],
        "reset": "IDLE",
    },
    "handshake_generator": {
        "title": "USBHandshakeGenerator",
        "states": ["IDLE", "TRANSMIT"],
        "reset": "IDLE",
    },
    "reset_sequencer": {
        "title": "USBResetSequencer",
        "states": [
            "INITIALIZE", "LS_FS_NON_RESET", "START_HS_DETECTION",
            "PREPARE_FOR_CHIRP_0", "PREPARE_FOR_CHIRP_1", "DEVICE_CHIRP",
            "AWAIT_HOST_K", "IN_HOST_K", "AWAIT_HOST_J", "IN_HOST_J",
            "IS_HIGH_SPEED", "HS_NON_RESET", "IS_LOW_OR_FULL_SPEED",
            "DETECT_HS_SUSPEND", "SUSPENDED", "DISCONNECT"
        ],
        "reset": "INITIALIZE",
    },
    "control_endpoint": {
        "title": "USBControlEndpoint (EP0)",
        "states": ["SETUP", "DATA_IN", "DATA_OUT", "STATUS_IN", "STATUS_OUT"],
        "reset": "SETUP",
    },
    "transfer_in": {
        "title": "USBInTransferManager",
        "states": ["WAIT_FOR_DATA", "WAIT_TO_SEND", "SEND_PACKET", "WAIT_FOR_ACK"],
        "reset": "WAIT_FOR_DATA",
    },
    "descriptor_generator": {
        "title": "USBDescriptorStreamGenerator",
        "states": ["IDLE", "STREAMING", "DONE"],
        "reset": "IDLE",
    },
    "stream_boundary": {
        "title": "USBOutStreamBoundaryDetector",
        "states": ["WAIT_FOR_FIRST", "RECEIVE_AND_SEND", "OUTPUT_STROBES"],
        "reset": "WAIT_FOR_FIRST",
    },
}

for key, fsm in FSMS.items():
    g = graphviz.Digraph(key, graph_attr=SKIN)
    g.attr(label=f"State Machine: {fsm['title']}", labelloc="t", fontsize="12")

    states = fsm["states"]
    reset = fsm["reset"]

    # Add states
    for s in states:
        attrs = {"shape": "box", "style": "rounded,filled", "fillcolor": "#e8e8e8"}
        if s == reset:
            attrs["fillcolor"] = "#c0e0c0"  # green tint for reset state
            attrs["peripheries"] = "2"
        if "DONE" in s or "COMPLETE" in s:
            attrs["fillcolor"] = "#e0c0c0"  # red-ish for terminal
        g.node(s, label=s.replace("_", " "), **attrs)

    # Draw transitions (linear for simplicity; some have looping/multiple paths)
    for i in range(len(states) - 1):
        g.edge(states[i], states[i + 1])

    # Loopbacks to IDLE for terminal/complete states
    for s in states:
        if "COMPLETE" in s or "DONE" in s:
            if "IDLE" in states:
                g.edge(s, "IDLE", style="dashed", color="#888")

    # Self-loops for idle
    g.edge("IDLE", "IDLE", "rx_active=0", style="dashed", color="#888")

    g.render(filename=key, directory=OUT, format="svg", cleanup=True)
    print(f"  {key}.svg")

print(f"\nAll FSM diagrams generated in {OUT}/")
