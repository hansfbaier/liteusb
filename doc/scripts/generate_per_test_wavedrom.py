#!/usr/bin/env python3
"""Generate additional per-test-method WaveDrom JSONs for section 10."""
import json, os

OUT = 'doc/wavedrom'
os.makedirs(OUT, exist_ok=True)

# New per-test-method diagrams (complementing the existing general ones)
# Each entry: (filename, wave_data)
additional = {
    # ── Token Detection: SOF token ──
    'token_detect_sof': {
        'head': {'text': 'test_valid_start_of_frame: SOF token, frame 0x53a'},
        'signal': [
            {'name': 'clk',      'wave': 'p...........'},
            {'name': 'rx_active', 'wave': '0.1.......0.'},
            {'name': 'rx_valid',  'wave': '0.1.......0.'},
            {'name': 'rx_data',   'wave': '2.3.4.5...2.', 'data': ['SYNC','PID=SOF','frame[7:0]','frame[10:8]','CRC5']},
            {'name': 'new_token', 'wave': '0..........'},
            {'name': 'new_frame', 'wave': '0...10......'},
            {'name': 'frame',     'wave': 'x...2x......', 'data': ['0x53a']},
        ]
    },
    # ── Token Detection: address mismatch ──
    'token_detect_mismatch': {
        'head': {'text': 'test_token_to_other_device: token to 0x3a, device at 0x1f (mismatch)'},
        'signal': [
            {'name': 'clk',      'wave': 'p...........'},
            {'name': 'rx_active', 'wave': '0.1.......0.'},
            {'name': 'rx_valid',  'wave': '0.1.......0.'},
            {'name': 'rx_data',   'wave': '2.3.4.5...2.', 'data': ['SYNC','PID=OUT','ADDR=0x3a','ENDP','CRC5']},
            {'name': 'new_token', 'wave': '0...........'},  # stays zero — filtered!
            {'name': 'address_d', 'wave': 'x.2.........x', 'data': ['0x1f']},
        ]
    },
    # ── Handshake Detection: NAK ──
    'handshake_nak': {
        'head': {'text': 'test_nak: NAK detection (PID 0x5A)'},
        'signal': [
            {'name': 'clk',          'wave': 'p......'},
            {'name': 'rx_active',    'wave': '0.10..0'},
            {'name': 'rx_valid',     'wave': '0.10..0'},
            {'name': 'rx_data',      'wave': '2.3.2..2', 'data': ['SYNC','0x5A(NAK)']},
            {'name': 'detected.nak', 'wave': '0..10..'},
            {'name': 'detected.ack', 'wave': '0......'},
        ]
    },
    # ── Handshake Detection: STALL ──
    'handshake_stall': {
        'head': {'text': 'test_stall: STALL detection (PID 0x1E)'},
        'signal': [
            {'name': 'clk',            'wave': 'p......'},
            {'name': 'rx_active',      'wave': '0.10..0'},
            {'name': 'rx_valid',       'wave': '0.10..0'},
            {'name': 'rx_data',        'wave': '2.3.2..2', 'data': ['SYNC','0x1E(STALL)']},
            {'name': 'detected.stall', 'wave': '0..10..'},
            {'name': 'detected.ack',   'wave': '0......'},
        ]
    },
    # ── Data Packet Receiver: ZLP ──
    'data_rx_zlp': {
        'head': {'text': 'test_zlp: Zero-Length Packet (DATA1 + CRC16 only)'},
        'signal': [
            {'name': 'clk',             'wave': 'p..........'},
            {'name': 'rx_active',       'wave': '0.1......0'},
            {'name': 'rx_valid',        'wave': '0.1.0...0'},
            {'name': 'rx_data',         'wave': '2.3.4...2', 'data': ['SYNC','PID=0x4B','CRC16_lo','CRC16_hi']},
            {'name': 'stream.valid',    'wave': '0.........'},
            {'name': 'packet_complete', 'wave': '0......10.'},
        ]
    },
    # ── Data Packet Deserializer: invalid CRC ──
    'data_deserialize_invalid': {
        'head': {'text': 'test_invalid_rx: corrupted CRC16 → new_packet=0'},
        'signal': [
            {'name': 'clk',       'wave': 'p............'},
            {'name': 'rx_active', 'wave': '0.1.........0'},
            {'name': 'rx_data',   'wave': '2.3.4.5.6...2', 'data': ['SYNC','PID','B0','B1','0xFF','0xFF']},
            {'name': 'new_packet','wave': '0............'},  # never pulses — CRC failed
            {'name': 'crc_valid', 'wave': '0............'},
        ]
    },
    # ── Data Packet Generator: single byte ──
    'data_gen_single': {
        'head': {'text': 'test_single_byte: 1-byte stream → 1-byte packet'},
        'signal': [
            {'name': 'clk',          'wave': 'p........'},
            {'name': 'stream.first', 'wave': '0.10.....'},
            {'name': 'stream.last',  'wave': '0.10.....'},
            {'name': 'stream.valid', 'wave': '0.10.....'},
            {'name': 'stream.payload','wave':'x.2x.....', 'data': ['0x55']},
            {'name': 'tx.valid',     'wave': '0..1....0'},
            {'name': 'tx.data',      'wave': 'x..4...x', 'data': ['PID','0x55','CRC']},
            {'name': 'stream.ready', 'wave': '0..1....0'},
        ]
    },
    # ── Data Packet Generator: ZLP ──
    'data_gen_zlp': {
        'head': {'text': 'test_zlp_generation: zero-length packet (first=0,last=1,valid=1,no payload)'},
        'signal': [
            {'name': 'clk',          'wave': 'p.......'},
            {'name': 'stream.first', 'wave': '0.0.....'},
            {'name': 'stream.last',  'wave': '0.10....'},
            {'name': 'stream.valid', 'wave': '0.10....'},
            {'name': 'stream.payload','wave':'x.x.....'},
            {'name': 'tx.valid',     'wave': '0..1...0'},
            {'name': 'tx.data',      'wave': 'x..4..x', 'data': ['PID=0xC3','CRC=0','CRC=0']},
            {'name': 'stream.ready', 'wave': '0..1...0'},
        ]
    },
    # ── Transfer Manager: NAK retransmission ──
    'transfer_nak_retransmit': {
        'head': {'text': 'test_normal_transfer: NAK → retransmit same PID'},
        'signal': [
            {'name': 'clk',       'wave': 'p................'},
            {'name': 'token.is_in','wave': '0.10....10........'},
            {'name': 'tx.valid',  'wave': '0...1.0...1.0.....'},
            {'name': 'data_pid',  'wave': 'x...2x...2x.......', 'data': ['DATA0','DATA0','DATA1']},
            {'name': 'ack',       'wave': '0............10....'},
            {'name': 'nak',       'wave': '0.....10..........'},
        ],
        'foot': {'text': 'First IN: NAKed → retransmit DATA0. Second IN: ACKed → advance to DATA1.'}
    },
    # ── Transfer Manager: ZLP not sent for short packet ──
    'transfer_zlp_behavior': {
        'head': {'text': 'test_zlp_generation: full last-packet → ZLP; short last-packet → no ZLP'},
        'signal': [
            {'name': 'clk',          'wave': 'p.....................'},
            {'name': 'transfer.last','wave': '0.....1.0...........1.'},
            {'name': 'token.is_in',  'wave': '0......1.0....1.0....1'},
            {'name': 'tx.valid',     'wave': '0.......1.0....1.0...1'},
            {'name': 'tx.last',      'wave': '0.......1.0....1.0...1'},
            {'name': 'packet_size',  'wave': 'x.......2x....3x....', 'data': ['8(full)','ZLP=0','3(short)']},
        ],
        'foot': {'text': 'Full 8-byte last → ZLP after. Short 3-byte last → finishes without ZLP.'}
    },
    # ── Transfer Manager: discard ──
    'transfer_discard': {
        'head': {'text': 'test_discard: discard drops queued packet'},
        'signal': [
            {'name': 'clk',          'wave': 'p................'},
            {'name': 'transfer.valid','wave': '0.10.....10......'},
            {'name': 'discard',      'wave': '0......10........'},
            {'name': 'token.is_in',  'wave': '0.......1.0.....1'},
            {'name': 'tx.valid',     'wave': '0...............1'},
            {'name': 'data_pid',     'wave': 'x.2........3....2','data': ['DATA0','DATA1','DATA0']},
            {'name': 'ack',          'wave': '0................1'},
        ],
        'foot': {'text': 'Packet1 ACKed → discard → Packet2 never sent. New buffer → DATA1.'}
    },
    # ── Reset: SE0 duration test ──
    'reset_se0_timing': {
        'head': {'text': 'test_full_speed_reset: SE0 held >2.5\u00b5s triggers bus_reset'},
        'signal': [
            {'name': 'clk',           'wave': 'p..................'},
            {'name': 'line_state',    'wave': '2.3...............2', 'data': ['0b01(J)','0b00(SE0)','0b01(J)']},
            {'name': 'bus_reset',     'wave': '0....10...........'},
            {'name': 'se0_timer',     'wave': 'x.2.3.4.5........x', 'data': ['1','2','3','4','...\u22652.5\u00b5s']},
            {'name': 'operating_mode','wave': 'x.2.3.............', 'data': ['NORMAL','CHIRP']},
        ]
    },
    # ── Request: FS interpacket delay ──
    'request_fs_delay': {
        'head': {'text': 'test_fs_interpacket_delay: 10-cycle interpacket gap at Full Speed'},
        'signal': [
            {'name': 'clk',        'wave': 'p...............'},
            {'name': 'speed',      'wave': '2...............', 'data': ['FULL(12Mbps)']},
            {'name': 'setup_pid',  'wave': '0.10............'},
            {'name': 'data_pid',   'wave': '0..........10..'},
            {'name': 'ack',        'wave': '0............10.'},
            {'name': 'delay_count','wave': 'x.3.4.5.6......x', 'data': ['1','2','3','4','...10']},
        ],
        'foot': {'text': '~10 cycles (2.5\u00b5s) between SETUP token and ACK handshake at FS.'}
    },
    # ── Request: truncated setup ──
    'request_truncated': {
        'head': {'text': 'test_short_setup_packet: truncated 4-byte setup → ignored'},
        'signal': [
            {'name': 'clk',           'wave': 'p.............'},
            {'name': 'rx_active',     'wave': '0.1...0......'},
            {'name': 'rx_data',       'wave': '2.3.4.......2', 'data': ['SETUP PID','DATA0 PID','B0','B1']},
            {'name': 'packet.received','wave': '0.............'},  # never pulses
            {'name': 'error',         'wave': '0........10...'},
        ]
    },
    # ── Request: valid sequence receive (per-test) ──
    'request_valid_sequence': {
        'head': {'text': 'test_valid_sequence_receive: Full SETUP transaction → decoded SetupPacket'},
        'signal': [
            {'name': 'clk','wave': 'p...................'},
            {'name': 'rx_active','wave': '0.1......0.1......0'},
            {'name': 'rx_data','wave': '2.3......2.3......2', 'data': ['SETUP PID','DATA0 PID','setup[0:7]','CRC']},
            {'name': 'ack','wave': '0.........10........'},
            {'name': 'packet.received','wave':'0..........10.......'},
            {'name': 'packet.request','wave':'x..........2x......', 'data': ['0x06(GET_DESC)']},
            {'name': 'packet.value','wave':'x..........2x......', 'data': ['0x0100(DEVICE)']},
            {'name': 'packet.index','wave':'x..........2x......', 'data': ['0x0000']},
            {'name': 'packet.length','wave':'x..........2x......', 'data': ['18']},
            {'name': 'packet.is_in_request','wave':'x........10.........'},
        ],
        'foot': {'text': 'SETUP token → DATA0 with GET_DESCRIPTOR(DEVICE, wLength=18) → ACK → decoded fields.'}
    },
    # ── Enumeration: full sequence ──
    'device_enumeration': {
        'head': {'text': 'test_enumeration: Full enumeration — GET_DESCRIPTOR, SET_ADDRESS, SET_CONFIG'},
        'signal': [
            {'name': 'clk',         'wave': 'p...................'},
            {'name': 'ep0_state',   'wave': '2.3.4.5.2.3.4.5.2.', 'data': ['SETUP','DATA_IN','STATUS_OUT','IDLE','SETUP','DATA_IN','STATUS_OUT','IDLE']},
            {'name': 'address',     'wave': '2.2.........3.......', 'data': ['0','0x31']},
            {'name': 'config',      'wave': '2.2.3...............', 'data': ['0','1']},
            {'name': 'descriptor_rq','wave':'2.2.2..............', 'data': ['DEVICE','CONFIG']},
        ],
        'foot': {'text': '1. GET_DESCRIPTOR(DEVICE) → 2. SET_ADDRESS(0x31) → 3. SET_CONFIGURATION(1)'}
    },
    # ── Data Packet Deserializer: normal capture ──
    'data_deserialize_packet_rx': {
        'head': {'text': 'test_packet_rx: PID + 4 data bytes + CRC16 → captured packet[0..3]'},
        'signal': [
            {'name': 'clk','wave': 'p................'},
            {'name': 'rx_active','wave': '0.1.............0'},
            {'name': 'rx_data','wave': '2.3.4.5.6.7.8.9.', 'data': ['SYNC','PID=0xC3','B0','B1','B2','B3','CRC_lo','CRC_hi']},
            {'name': 'new_packet','wave': '0..............10'},
            {'name': 'length','wave': 'x..............2x', 'data': ['4']},
            {'name': 'packet[0]','wave': 'x..............2x', 'data': ['B0']},
            {'name': 'packet[1]','wave': 'x..............3x', 'data': ['B1']},
            {'name': 'packet[2]','wave': 'x..............4x', 'data': ['B2']},
            {'name': 'packet[3]','wave': 'x..............5x', 'data': ['B3']},
        ]
    },
    # ── Data Packet Generator: simple generation ──
    'data_gen_simple': {
        'head': {'text': 'test_simple_data_generation: 8-byte stream → full packet with CRC16'},
        'signal': [
            {'name': 'clk','wave': 'p..............'},
            {'name': 'stream.first','wave': '0.10...........'},
            {'name': 'stream.last','wave': '0...........10'},
            {'name': 'stream.valid','wave': '0...........10'},
            {'name': 'stream.payload','wave':'x.4..........x', 'data': ['B0','B1','B2','...','B7']},
            {'name': 'tx.valid','wave': '0..1..........0'},
            {'name': 'tx.data','wave': 'x..5.........x', 'data': ['PID','B0..B7','CRC_lo','CRC_hi']},
            {'name': 'stream.ready','wave': '0..1..........0'},
        ],
        'foot': {'text': 'PID=0xC3 (DATA0) → 8 payload bytes → CRC16 (0xEB, 0xBC)'}
    },
    # ── Handshake Generator: already-ready case ──
    'handshake_gen_ready': {
        'head': {'text': 'test_already_ready: tx_ready=1 already high → single-cycle valid'},
        'signal': [
            {'name': 'clk','wave': 'p....'},
            {'name': 'issue_ack','wave': '0.10.'},
            {'name': 'tx.valid','wave': '0..10'},
            {'name': 'tx.data','wave': 'x..2x', 'data': ['0xD2(ACK)']},
            {'name': 'tx.ready','wave': '1....'},
        ]
    },
    # ── Transfer Manager: NAK-when-not-ready ──
    'transfer_nak_not_ready': {
        'head': {'text': 'test_nak_when_not_ready: IN token with empty buffer → NAK'},
        'signal': [
            {'name': 'clk','wave': 'p........'},
            {'name': 'token.is_in','wave': '0.10.....'},
            {'name': 'tx.valid','wave': '0........'},
            {'name': 'nak','wave': '0..10....'},
            {'name': 'ack','wave': '0........'},
            {'name': 'active','wave': '0........'},
        ]
    },
    # ── Loopback: OUT→IN flow ──
    'integration_loopback': {
        'head': {'text': 'test_usb2_loopback: OUT → bulk endpoint → IN loopback'},
        'signal': [
            {'name': 'clk',       'wave': 'p..................'},
            {'name': 'token_pid', 'wave': '2.3..2..3.........2', 'data': ['OUT','DATA0','IN','DATA1']},
            {'name': 'rx_data',   'wave': 'x.2x..............x', 'data': ['pkt']},
            {'name': 'tx_data',   'wave': 'x.......2.........x', 'data': ['pkt(echo)']},
            {'name': 'ack',       'wave': '0....1.0.1..........'},
        ],
        'foot': {'text': 'OUT token → DATA0 packet → ACK. IN token → DATA1 packet (echo) → ACK.'}
    },
    # ── Stress test: constant IN stream ──
    'integration_stress': {
        'head': {'text': 'test_usb2_stress: constant IN streaming, DATA toggle across 4 rounds'},
        'signal': [
            {'name': 'clk',       'wave': 'p......................'},
            {'name': 'token.is_in','wave': '0.10.10.10.10.........'},
            {'name': 'tx.valid',  'wave': '0..10..10..10..10......'},
            {'name': 'data_pid',  'wave': 'x..2...3...2...3.......', 'data': ['DATA0','DATA1','DATA0','DATA1']},
            {'name': 'tx.payload','wave': 'x..2...3...2...3.......', 'data': ['pkt0','pkt1','pkt2','pkt3']},
            {'name': 'ack',       'wave': '0...1...1...1...1......'},
        ],
        'foot': {'text': 'Four IN tokens, four packets, DATA0/DATA1 toggle, all-zero payload verified.'}
    },
    # ── Token Detection: general valid token (complementing SOF + mismatch) ──
    'token_detect_valid': {
        'head': {'text': 'test_valid_token: OUT token to address 0x3a, endpoint 0x0a'},
        'signal': [
            {'name': 'clk','wave': 'p...........'},
            {'name': 'rx_active','wave': '0.1.......0.'},
            {'name': 'rx_valid','wave': '0.1.......0.'},
            {'name': 'rx_data','wave': '2.3.4.5...2.', 'data': ['SYNC','PID=OUT','addr=0x3a','endp=0x0a','CRC5']},
            {'name': 'new_token','wave': '0...10......'},
            {'name': 'pid','wave': 'x...2x......', 'data': ['0x87(OUT)']},
            {'name': 'address','wave': 'x...3x......', 'data': ['0x3a']},
            {'name': 'endpoint','wave': 'x...2x......', 'data': ['0x0a']},
        ]
    },
    # ── Interpacket Timer: FS timing ──
    'timer_resets_and_delays': {
        'head': {'text': 'test_resets_and_delays: FS interpacket timing → tx_allowed, tx_timeout, rx_timeout'},
        'signal': [
            {'name': 'clk','wave': 'p................................'},
            {'name': 'start','wave': '0.10...........................'},
            {'name': 'tx_allowed','wave': '0..........10...............'},
            {'name': 'tx_timeout','wave': '0........................10.'},
            {'name': 'rx_timeout','wave': '0.................................10'},
        ],
        'foot': {'text': 'FS speed: tx_allowed at ~10 cycles, tx_timeout at ~32, rx_timeout at ~80.'}
    },
    # ── ULPI: idle behavior ──
    'ulpi_idle_behavior': {
        'head': {'text': 'test_idle_behavior: ULPIRegisterWindow idle state — NOP on data_out'},
        'signal': [
            {'name': 'clk','wave': 'p.....'},
            {'name': 'busy','wave': '0.....'},
            {'name': 'ulpi_data_out','wave': '2.....', 'data': ['0x00(NOP)']},
            {'name': 'read_request','wave': '0.....'},
            {'name': 'write_request','wave': '0.....'},
            {'name': 'done','wave': '0.....'},
        ]
    },
    # ── ULPI: interrupted read ──
    'ulpi_interrupted_read': {
        'head': {'text': 'test_interrupted_read: DIR mid-read → ulpi_out_req dropped, then re-driven'},
        'signal': [
            {'name': 'clk','wave': 'p................'},
            {'name': 'read_request','wave': '0.10.............'},
            {'name': 'busy','wave': '0.1..........0...'},
            {'name': 'ulpi_out_req','wave': '0.1.0..1......0.'},
            {'name': 'ulpi_dir','wave': '0....10..0.......'},
            {'name': 'ulpi_data_out','wave':'x.2.3..2.......x', 'data': ['CMD','NOP','CMD(re-drv)']},
            {'name': 'ulpi_nxt','wave': '0........10......'},
            {'name': 'read_data','wave': 'x.........2....x', 'data': ['0x07']},
            {'name': 'done','wave': '0.........10.....'},
        ],
        'foot': {'text': 'DIR high → ulpi_out_req dropped. DIR low → command re-driven → read completes.'}
    },
    # ── ULPI: RxEvent decode ──
    'ulpi_decode': {
        'head': {'text': 'test_decode: ULPIRxEventDecoder — DIR+NXT ignored; DIR alone → rx_active'},
        'signal': [
            {'name': 'clk','wave': 'p..........'},
            {'name': 'ulpi_dir','wave': '0.1.0.1....'},
            {'name': 'ulpi_nxt','wave': '0.1.0.0....'},
            {'name': 'ulpi_data_i','wave':'x.2.3.4x..', 'data': ['data','rx_cmd']},
            {'name': 'last_rx_command','wave':'x.2.2.3x..', 'data': ['N/A','0x1E']},
            {'name': 'line_state','wave':'x.2.2.3x..', 'data': ['XX','0b10']},
            {'name': 'vbus_valid','wave':'0.......1.'},
            {'name': 'rx_active','wave':'0.......10'},
        ],
        'foot': {'text': 'DIR+NXT together → ignored. DIR only → RxCmd decoded: line_state, vbus, rx_active.'}
    },
    # ── ULPI: multi-write control ──
    'ulpi_multiwrite': {
        'head': {'text': 'test_multiwrite_behavior: ULPIControlTranslator — func ctrl + OTG ctrl'},
        'signal': [
            {'name': 'clk','wave': 'p....................'},
            {'name': 'op_mode','wave': '2.2.2..3............', 'data': ['00','01','10']},
            {'name': 'dp_pulldown','wave': '0......1...........'},
            {'name': 'dm_pulldown','wave': '0......1...........'},
            {'name': 'reg_busy','wave':'0.1.0....1.0.......'},
            {'name': 'reg_addr','wave':'x.2x....3x.......', 'data': ['0x04(func)','0x0A(OTG)']},
            {'name': 'reg_wdata','wave':'x.2x....3x...', 'data': ['0x59','0x00']},
            {'name': 'done','wave': '0.......10.....10....'},
        ],
        'foot': {'text': 'Two sequenced writes: function control (0x04→0x59) then OTG control (0x0A→0x00).'}
    },
    # ── ULPI: simple transmit (per-test) ──
    'ulpi_simple_transmit': {
        'head': {'text': 'test_simple_transmit: ULPITransmitTranslator SOF packet (0xA5)'},
        'signal': [
            {'name': 'clk','wave': 'p........'},
            {'name': 'tx_valid','wave': '0.10.....'},
            {'name': 'tx_data','wave': 'x.2x.....', 'data': ['0xA5']},
            {'name': 'ulpi_data_out','wave':'x.2.3....', 'data': ['CMD|PID','0x11']},
            {'name': 'ulpi_out_req','wave': '0.10.....'},
            {'name': 'ulpi_nxt','wave': '0..10....'},
            {'name': 'ulpi_stp','wave': '0....10..'},
            {'name': 'tx_ready','wave': '0..10....'},
        ]
    },
    # ── ULPI: handshake transmit ──
    'ulpi_handshake': {
        'head': {'text': 'test_handshake: ULPITransmitTranslator ACK handshake (PID 0xD2)'},
        'signal': [
            {'name': 'clk','wave': 'p........'},
            {'name': 'tx_valid','wave': '0.10.....'},
            {'name': 'tx_data','wave': 'x.2x.....', 'data': ['0xD2(ACK)']},
            {'name': 'ulpi_data_out','wave':'x.2.3....', 'data': ['CMD|PID','NOP']},
            {'name': 'ulpi_out_req','wave': '0.10..0..'},
            {'name': 'ulpi_nxt','wave': '0..10....'},
            {'name': 'ulpi_stp','wave': '0....10..'},
            {'name': 'tx_ready','wave': '0..10....'},
        ],
        'foot': {'text': 'TRANSMIT_COMMAND with ACK PID → NXT → STP → idle.'}
    },
    # ── ULPI: register read flow ──
    'ulpi_register_read': {
        'head': {'text': 'test_register_read: ULPIRegisterWindow read flow (REG_READ=0xC0)'},
        'signal': [
            {'name': 'clk','wave': 'p...........'},
            {'name': 'read_request','wave': '0.10........'},
            {'name': 'busy','wave': '0.1.........0'},
            {'name': 'ulpi_data_out','wave': 'x.2.3.4....x', 'data': ['0xC0|addr','NOP','NOP(turn)']},
            {'name': 'ulpi_out_req','wave': '0.1......0.'},
            {'name': 'ulpi_nxt','wave': '0..10.......'},
            {'name': 'ulpi_dir','wave': '0............'},
            {'name': 'read_data','wave': 'x.......2..x', 'data': ['0x07']},
            {'name': 'done','wave': '0.......10..'},
        ],
        'foot': {'text': 'Command phase (REG_READ|addr) → NXT → turnaround → data latch → done strobe.'}
    },
    # ── ULPI: register write flow ──
    'ulpi_register_write': {
        'head': {'text': 'test_register_write: ULPIRegisterWindow write flow (REG_WRITE=0x80)'},
        'signal': [
            {'name': 'clk','wave': 'p............'},
            {'name': 'write_request','wave': '0.10.........'},
            {'name': 'busy','wave': '0.1..........0'},
            {'name': 'ulpi_data_out','wave': 'x.2.3.3.4...x', 'data': ['0x80|addr','data','NOP','STP']},
            {'name': 'ulpi_out_req','wave': '0.1.......0.'},
            {'name': 'ulpi_nxt','wave': '0..10........'},
            {'name': 'ulpi_stop','wave': '0......10....'},
            {'name': 'done','wave': '0........10..'},
        ],
        'foot': {'text': 'Command → data → NXT → STOP → done. addr=0x02, data=0xBC.'}
    },
    # ── Device: long descriptor (30 endpoints) ──
    'device_long_descriptor': {
        'head': {'text': 'test_long_descriptor: Configuration descriptor with 30 endpoints'},
        'signal': [
            {'name': 'clk','wave': 'p....................'},
            {'name': 'tx.valid','wave': '0..1...............0'},
            {'name': 'tx.first','wave': '0..10...............'},
            {'name': 'tx.last','wave': '0..................10'},
            {'name': 'tx.payload','wave':'x..2...............x', 'data': ['cfg[0:8]','...','cfg[end-1]']},
            {'name': 'descriptor_length','wave':'x.2...............x', 'data': ['512+']},
        ],
        'foot': {'text': 'Long config descriptor (15 IN + 15 OUT endpoints) spans multiple packets.'}
    },
}

for name, data in additional.items():
    path = f'{OUT}/{name}.json'
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

print(f'{len(additional)} additional per-test diagrams written')
