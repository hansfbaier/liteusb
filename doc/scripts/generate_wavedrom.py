#!/usr/bin/env python3
"""Generate WaveDrom JSON files for liteusb documentation."""
import json, os

OUT = 'doc/wavedrom'
os.makedirs(OUT, exist_ok=True)

diagrams = {
    'token_detect': {
        'head': {'text': 'USBTokenDetector — OUT token to address 0x3a'},
        'signal': [
            {'name': 'clk',     'wave': 'p...........'},
            {'name': 'rx_active','wave': '0.1.......0.'},
            {'name': 'rx_valid', 'wave': '0.1.......0.'},
            {'name': 'rx_data',  'wave': '2.3.4.5...2.', 'data': ['SYNC','PID=OUT','ADDR[0:6]','ENDP[0:3]','CRC5']},
            {'name': 'new_token','wave': '0...10......'},
            {'name': 'pid',      'wave': 'x...2x......', 'data': ['0x87(OUT)']},
            {'name': 'address',  'wave': 'x...3x......', 'data': ['0x3a']},
            {'name': 'endpoint', 'wave': 'x...2x......', 'data': ['0x0a']},
        ]
    },
    'handshake_detect': {
        'head': {'text': 'USBHandshakeDetector — ACK detection'},
        'signal': [
            {'name': 'clk',           'wave': 'p......'},
            {'name': 'rx_active',     'wave': '0.10..0'},
            {'name': 'rx_valid',      'wave': '0.10..0'},
            {'name': 'rx_data',        'wave': '2.3.2..2', 'data': ['SYNC','0xD2(ACK)']},
            {'name': 'detected.ack',   'wave': '0..10..'},
            {'name': 'detected.nak',   'wave': '0......'},
            {'name': 'detected.stall', 'wave': '0......'},
        ]
    },
    'data_rx': {
        'head': {'text': 'USBDataPacketReceiver — DATA0 + 8 bytes + CRC16'},
        'signal': [
            {'name': 'clk',             'wave': 'p..............'},
            {'name': 'rx_active',       'wave': '0.1...........0'},
            {'name': 'rx_valid',        'wave': '0.1.0.....1...0'},
            {'name': 'rx_data',         'wave': '2.3.4.........2', 'data': ['SYNC','PID=0xC3','payload+CRC','idle']},
            {'name': 'stream.valid',    'wave': '0........10....'},
            {'name': 'stream.next',     'wave': '0........1.0...'},
            {'name': 'stream.payload',  'wave': 'x........2x....', 'data': ['0xAA']},
            {'name': 'packet_complete', 'wave': '0.........10...'},
            {'name': 'active_pid',      'wave': 'x........2x....', 'data': ['DATA0']},
        ]
    },
    'data_deserialize': {
        'head': {'text': 'USBDataPacketDeserializer — 4-byte packet capture'},
        'signal': [
            {'name': 'clk',       'wave': 'p............'},
            {'name': 'rx_active', 'wave': '0.1.........0'},
            {'name': 'rx_data',   'wave': '2.3.4.5.6...2', 'data': ['SYNC','PID','B0','B1','B2','CRC']},
            {'name': 'new_packet','wave': '0........10..'},
            {'name': 'length',    'wave': 'x........2x..', 'data': ['4']},
            {'name': 'packet[0]', 'wave': 'x........2x..', 'data': ['B0']},
            {'name': 'packet[1]', 'wave': 'x........3x..', 'data': ['B1']},
            {'name': 'packet[2]', 'wave': 'x........4x..', 'data': ['B2']},
            {'name': 'packet[3]', 'wave': 'x........5x..', 'data': ['B3']},
        ]
    },
    'data_gen': {
        'head': {'text': 'USBDataPacketGenerator — 8-byte stream to TX packet'},
        'signal': [
            {'name': 'clk',          'wave': 'p.............'},
            {'name': 'stream.first', 'wave': '0.10..........'},
            {'name': 'stream.last',  'wave': '0...........10'},
            {'name': 'stream.valid', 'wave': '0...........10'},
            {'name': 'stream.payload','wave':'x.4..........x', 'data': ['0xAA','0xBB','...']},
            {'name': 'tx.valid',     'wave': '0..1.........0'},
            {'name': 'tx.data',      'wave': 'x..4.........x', 'data': ['PID=0xC3','8 bytes','CRC16']},
            {'name': 'stream.ready', 'wave': '0..1.........0'},
        ]
    },
    'handshake_gen': {
        'head': {'text': 'USBHandshakeGenerator — issue_ack strobe'},
        'signal': [
            {'name': 'clk',       'wave': 'p.....'},
            {'name': 'issue_ack', 'wave': '0.10..'},
            {'name': 'tx.valid',  'wave': '0..10.'},
            {'name': 'tx.data',   'wave': 'x..2x..', 'data': ['0xD2(ACK)']},
            {'name': 'tx.ready',  'wave': '0...10'},
        ]
    },
    'reset_seq': {
        'head': {'text': 'USBResetSequencer — Full Speed Reset to HS Detection'},
        'signal': [
            {'name': 'clk',           'wave': 'p.............'},
            {'name': 'line_state',    'wave': '2.3..........2', 'data': ['0b01(J)','0b00(SE0)','0b01(J)']},
            {'name': 'vbus_connected','wave': '0.1...........'},
            {'name': 'bus_reset',     'wave': '0....10.......'},
            {'name': 'current_speed', 'wave': 'x.2...2x...2x.', 'data': ['FULL','HIGH']},
            {'name': 'operating_mode','wave': 'x.2.....2x....', 'data': ['NORMAL','CHIRP']},
        ]
    },
    'control_ep0': {
        'head': {'text': 'USBControlEndpoint — EP0 stages: SETUP, DATA_IN, STATUS_OUT'},
        'signal': [
            {'name': 'clk',           'wave': 'p...............'},
            {'name': 'setup.received','wave': '0.10............'},
            {'name': 'fsm_state',     'wave': '2.3.4.5.2.......', 'data': ['SETUP','DATA_IN','STATUS_OUT','IDLE']},
            {'name': 'rx.valid',      'wave': '0.10.0..10.0....'},
            {'name': 'tx.valid',      'wave': '0..0..1.0..0.10.'},
            {'name': 'tx.first',      'wave': '0..0..10.0.0....'},
            {'name': 'tx.last',       'wave': '0...........10..'},
        ]
    },
    'transfer_in': {
        'head': {'text': 'USBInTransferManager — Double-buffered IN with PID toggle'},
        'signal': [
            {'name': 'clk',          'wave': 'p...............'},
            {'name': 'transfer.valid','wave': '0.10...10......'},
            {'name': 'transfer.last', 'wave': '0.....10.......'},
            {'name': 'token.is_in',  'wave': '0......10....10'},
            {'name': 'data_pid',     'wave': 'x.2........3..2', 'data': ['DATA0','DATA1','DATA0']},
            {'name': 'tx.valid',     'wave': '0.......10....1'},
            {'name': 'tx.payload',   'wave': 'x.......2x....2', 'data': ['pkt[0]','pkt[1]']},
            {'name': 'ack',          'wave': '0........10..'},
            {'name': 'nak',          'wave': '0.............'},
        ]
    },
    'descriptor': {
        'head': {'text': 'USBDescriptorStreamGenerator — ROM data to USB stream'},
        'signal': [
            {'name': 'clk',       'wave': 'p..........'},
            {'name': 'start',     'wave': '0.10.......'},
            {'name': 'tx.valid',  'wave': '0..1......0'},
            {'name': 'tx.first',  'wave': '0..10......'},
            {'name': 'tx.last',   'wave': '0.....1...0'},
            {'name': 'tx.payload','wave': 'x..3......x', 'data': ['desc[0]','desc[1]','desc[n-1]']},
            {'name': 'tx.ready',  'wave': '0..1......0'},
            {'name': 'stall',     'wave': '0..........'},
        ]
    },
    'setup_decoder': {
        'head': {'text': 'USBSetupDecoder — 8-byte setup packet decode'},
        'signal': [
            {'name': 'clk',           'wave': 'p...............'},
            {'name': 'rx_active',     'wave': '0.1......0.1..0'},
            {'name': 'rx_data',       'wave': '2.3......2.3..2', 'data': ['SETUP PID','DATA0 PID','setup[0:7]','CRC']},
            {'name': 'ack',           'wave': '0.........10....'},
            {'name': 'packet.received','wave':'0..........10...'},
        ]
    },
    'stream_boundary': {
        'head': {'text': 'USBOutStreamBoundaryDetector — first/last byte detection'},
        'signal': [
            {'name': 'clk',          'wave': 'p..........'},
            {'name': 'unproc.valid', 'wave': '0.1......0.'},
            {'name': 'unproc.payload','wave':'x.4......x.', 'data': ['0xAA','0xBB','0xCC','0xDD']},
            {'name': 'proc.valid',   'wave': '0...1.....0'},
            {'name': 'proc.payload', 'wave': 'x...4....x.', 'data': ['0xAA','0xBB','0xCC','0xDD']},
            {'name': 'first',        'wave': '0....10....'},
            {'name': 'last',         'wave': '0........10'},
        ]
    },
    'ulpi_transmit': {
        'head': {'text': 'ULPITransmitTranslator — SOF packet transmission'},
        'signal': [
            {'name': 'clk',          'wave': 'p........'},
            {'name': 'tx_valid',     'wave': '0.10.....'},
            {'name': 'tx_data',      'wave': 'x.2x.....', 'data': ['0xA5(SOF)']},
            {'name': 'ulpi_data_out','wave': 'x.2.3....', 'data': ['CMD|PID','0x11']},
            {'name': 'ulpi_out_req', 'wave': '0.10.....'},
            {'name': 'ulpi_nxt',     'wave': '0..10....'},
            {'name': 'ulpi_stp',     'wave': '0....10..'},
            {'name': 'tx_ready',     'wave': '0..10....'},
        ]
    },
}

for name, d in diagrams.items():
    with open(f'{OUT}/{name}.json', 'w') as f:
        json.dump(d, f, indent=2)
    print(f'  {name}.json')
print(f'\n{len(diagrams)} files written')
