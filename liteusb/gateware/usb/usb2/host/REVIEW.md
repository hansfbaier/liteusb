# EHCI Host Controller — Implementation Review

Review of the `ehci` branch host controller (`liteusb/gateware/usb/usb2/host/`
plus `examples/deca_ehci_host.py` and `examples/firmware/`), checked against

- the **EHCI Specification Rev 1.0** (register map §2, data structures §3,
  operational model §4),
- the **USB 2.0 Specification** (§7.1.7 reset/chirp, §8.3 CRC, §8.5 transfers),
- the **Linux kernel EHCI driver** (`drivers/usb/host/ehci.h`, `ehci_def.h`,
  `ehci-hub.c` semantics).

All gateware bugs marked **FIXED** are corrected in this working tree and
covered by the rewritten test suite (`liteusb/tests/test_ehci_*.py`,
70 tests, all passing). Issues marked **OPEN** remain.

---

## 1. Verdict

The original implementation did **not** conform to the EHCI specification
and could not have worked with the Linux driver or real hardware:

- no EHCI capability registers were readable (Linux reads `CAPLENGTH` first;
  it would have read USBCMD bits instead),
- the schedule processor was a stub that never touched memory — no QH/qTD
  traversal exists, so no DMA-based transfer could ever execute,
- the token CRC-5 was wrong on 1984 of 2048 inputs,
- the bus reset did not drive SE0 correctly, and the host chirp sequence
  was inverted and raced the device chirp,
- the register file, transfer engine, and top level had multiple
  multi-driver/undriven-signal faults that break synthesis or hang FSMs,
- 22 of the 44 original tests failed; several tests asserted the wrong
  expected values (e.g. CRC5 of all-zeros as `0x0C`; correct is `0x02`).

After the fixes in this tree, every module is unit-correct against its
spec-level contract (see §7 for what the tests prove). The controller is
still **not** a complete EHCI HC: the DMA schedule engine (§6.1) is the
remaining gating feature.

---

## 2. Register file (`registers.py`) — EHCI §2, Linux `ehci_def.h`

### FIXED

1. **No capability registers.** Linux `ehci_platform_probe` → `ehci_setup`
   reads `CAPLENGTH` at offset 0 and computes the operational base from it.
   The old file decoded USBCMD at offset 0x00 and never exposed
   CAPLENGTH/HCIVERSION/HCSPARAMS/HCCPARAMS. Now: capability block at
   0x00–0x1F (`CAPLENGTH=0x20`, `HCIVERSION=0x0100`, HCSPARAMS with
   N_PORTS + PPC, HCCPARAMS=0), operational block at +0x20 exactly as
   `struct ehci_regs` expects (USBCMD 0x20 … ASYNCLISTADDR 0x38,
   CONFIGFLAG 0x60, PORTSC 0x64).
2. **USBSTS broken RW1C.** Hardware status overwrote the register every
   cycle, so status bits neither latched nor cleared. Now: sticky bits
   [5:0] set by hardware strobes, cleared by write-1, HW-set wins ties;
   HCHalted/Reclamation/PSS/ASS are read-only live bits, as in §2.2.2.
3. **HCRESET did not self-clear.** EHCI §2.2.1 + Linux `ehci_reset()`
   require the HC to clear the bit when reset completes. Now cleared by a
   `hc_reset_done` strobe.
4. **IAA doorbell did not auto-clear** (§2.2.1/§4.8). Fixed.
5. **FRINDEX** ignored `frame_index_in`. Now follows the schedule engine
   while running; writable only while halted (§2.2.4, Linux
   `ehci_run()`/`ehci_halt()` expectations).
6. **PORTSC** was a bare RW word. Now per-port registers with correct
   semantics: CCS/OCA/line-status read-only; CSC/PEC/OCC write-1-to-clear
   set by hardware strobes; PE set by hardware on reset completion,
   cleared by software write of 0 or disconnect; PR drives a port-reset
   output; PP resets to 1; WKOC_E/WKDSCNNT_E/WKCNNT_E implemented at the
   correct bit positions (the old `data_structures.py` had the three wake
   bits in reversed order; a `PORTSC_HSP` bit 9 was invented — EHCI
   reserves bit 9; both fixed).
7. **Wake/PIC/PTC bit offsets** corrected to match `ehci_def.h`
   (`PORT_WKCONN_E=20`, `PORT_WKDISC_E=21`, `PORT_WKOC_E=22`,
   `PORT_LED=14:15`, `PORT_TEST=19:16`).
8. **UTMI vs EHCI line-status encoding** mismatch (UTMI 01=J/10=K,
   EHCI 01=K/10=J) — `ehci.py` now maps between them instead of wiring
   the raw UTMI value into PORTSC.

### OPEN (minor)

- Suspend/resume (FPR, SUSP) and port test modes (PTC) are stored but do
  not drive PHY behavior. Linux does not need them for basic operation.
- `USBINTR` interrupt threshold (ITC) is stored but not functional
  (interrupts are generated per event, i.e. ITC=1 behavior).

---

## 3. Token generation (`token_generator.py`) — USB 2.0 §8.3

### FIXED

1. **CRC-5 algorithm wrong on 1984/2048 inputs.** The old code ran an
   MSB-first polynomial division; USB CRC-5 is LSB-first with init
   all-ones and complemented output (§8.3.5). Rewritten as an unrolled
   LSB-first LFSR; verified against a bit-serial reference across the
   input space and against the known wire capture `SETUP addr0 ep0 =
   2D 00 10`.
2. **`crc5_value` undriven** (X in simulation, 0 in synthesis) — the token
   CRC byte would have been garbage. Connected to the CRC5 output.
3. **CRC/payload nibble order in byte 2 swapped** — wire format is
   `CRC5<<3 | payload[10:8]`; the old `Cat(crc5, payload[8:11])` put the
   CRC in the low bits.
4. **ADDR/ENDP order swapped** — wire format is ADDR in bits [6:0],
   ENDP in [10:7]; the old `Cat(endpoint, address)` reversed them.
5. **SPLIT token construction truncated** a 19-bit payload into 11 bits.
   SPLIT removed entirely: with an integrated root-hub TT, SPLIT tokens
   never appear on the wire (they exist only between an HC and a hub TT).
6. **`sof_hold` froze the counter**, so a held SOF was lost. Now the SOF
   is queued and fires immediately on release (EHCI §4.6 note on TT
   think time; see also `sof_pending` logic).
7. **Microframe counter never wrapped** (7 → 0 missing).
8. **FSM ran in the `sys` domain** while the rest of the module is `usb`
   — a CDC bug. All host FSMs moved to the `usb` domain.

---

## 4. Transfer engine (`transfer.py`) — EHCI §4.10, qTD semantics §3.5.3

### FIXED

1. **`timeout_counter` never loaded** — WAIT_DATA/WAIT_HANDSHAKE timed out
   on cycle 0, so every IN/OUT would fail. Timeouts are now loaded on
   state entry, per-speed configurable.
2. **`transfer_active` undriven** (used in a sync clear block). Removed;
   response fields are now single-driver registered pulses.
3. **Duplicate packet-layer submodules** (`data_tx`, `data_rx`, `hs_det`,
   timer) inside the engine, unconnected to the UTMI bus — the engine
   could never have sent or received a byte. The engine now takes the
   shared components from the parent, exactly as wired in `ehci.py`.
4. **NAK consumed an error retry.** EHCI §3.5.3/§4.10: NAK retires the
   transaction without touching Cerr. Now NAK completes with `nak` and
   does not count against the retry budget; only timeout/CRC errors do.
5. **`bytes_xfer` read from `data_rx.stream.payload`** (a byte value, not
   a count). Now counts received stream bytes; babble detection compares
   against `max_packet`.
6. **No zero-length-packet support and no length field.** The request
   record now carries `length`; ZLPs are emitted as PID + CRC only.
7. **IN-phase ACK never sent** (comment said "drive it directly"; nothing
   did). The engine now pulses the handshake generator.
8. **Response record double-driven** (FSM comb + sync defaults). Single
   driver now.
9. **Token issue race** — the engine could exit ISSUE_TOKEN before the
   token generator started. Now waits for `token_busy` to rise and fall.
10. **Address/endpoint latched** at request accept (the request record is
    allowed to change mid-transfer).

### Test-proven flows

OUT with payload (DATA0 PID, payload, CRC16 on the wire), IN with CRC
check + host ACK + byte count, NAK (no retry), STALL, handshake timeout
retry-then-error with exact retry count, ZLP, SETUP always DATA0.

---

## 5. Reset & speed detection (`reset_host.py`) — USB 2.0 §7.1.7.3/7.1.7.5

### FIXED

1. **SE0 was not driven correctly.** The old code sent 0x00 data bytes in
   NORMAL op-mode for 10 ms, which is not a bus reset. Now the
   transmitter is put in NON_DRIVING mode so the 15k pull-downs create
   SE0 (the standard UTMI technique).
2. **Chirp sequencing inverted and unsafe.** The old code drove chirp J
   first, immediately upon seeing the device chirp — while the device was
   still driving (bus contention). Per §7.1.7.5 the host must wait for
   the device chirp K to end, then drive K/J pairs starting with K
   (~50 µs each, ≥3 pairs). Implemented exactly so.
3. **LS device vs HS chirp disambiguation missing.** An LS pull-up (K)
   looks like a chirp K. Now distinguished by duration: chirp K lasts
   1–7 ms; a persistent K beyond T_DCHIRP is classified Low-Speed.
4. **100 ms FS/LS wait** replaced with the spec-conformant short
   detection window.

---

## 6. Schedule processor (`schedule.py`) — EHCI §3, §4

### 6.1 OPEN (gating): no DMA schedule walk

There is no bus master and no QH/qTD/iTD/siTD memory access anywhere in
the gateware. Consequences:

- The firmware/Linux driver programming model (frame list, async list,
  qTD overlays, write-backs) **cannot execute transfers**; the
  controller is not yet a functional EHCI HC for real workloads.
- `PERIODICLISTBASE`/`ASYNCLISTADDR` are stored and decoded but unused.
- `data_structures.py` documents the EHCI §3 layouts correctly (after
  the DWORD-1 comment fix) and is consistent with `struct ehci_qh` /
  `struct ehci_qtd` in Linux — the foundation for a DMA engine is there.

Until a DMA-backed schedule walker exists, throughput claims and the
Linux-driver README must be treated as aspirational. The fabricated
performance table in the README was removed.

### FIXED

- The stub asserted `transfer_request.valid` with uninitialized fields —
  this would have issued garbage tokens onto the bus. It now never
  issues requests; PSS/ASS status bits still pulse per §2.2.2.
- FRINDEX advance and HCHalted behavior pinned by tests.

---

## 7. Transaction translator (`transaction_translator.py`)

### FIXED

1. **TX byte advance used `rx_active`** (a receive signal) instead of
   `tx_ready` — tokens could not transmit. Fixed; `utmi_tx_ready` input
   added and wired in `ehci.py`.
2. **Handshake decode raced a single registered byte**; now decodes
   `utmi_rx_data` directly when `rx_valid` strobes.
3. **IN phase ended after the first byte**; now streams bytes until EOP.
4. Same ADDR/ENDP swap as the token generator (fixed).

### OPEN

- The TT's OUT/SETUP data phase does not append CRC-16 (a shared
  `USBDataPacketCRC` should be plumbed in).
- No PRE-PID generation for Low-Speed (§8.6.5).
- In the current top level the TT is intentionally idle: root-port FS/LS
  devices are served natively by switching the PHY speed
  (`xcvr_select`/`term_select` follow `port_speed` in `ehci.py`), which
  is the correct architecture for an integrated TT. The TT block remains
  for future hub-mode/HS-port operation.

---

## 8. Top level (`ehci.py`)

### FIXED

1. **Double comb driver on `token_gen.sof_enable`** (`1` and `regs.run`).
   Now driven by `regs.run` only.
2. **Schedule engine and transfer engine were not connected at all**
   (request/response records floated). Now connected.
3. **PORTSC status** was a hardcoded `Cat` with invented bits; now fed
   from real port state (connect, edge-detected connect-change for
   CSC/PCD, PE from reset completion, mapped line status).
4. **Connect detection** only watched J; LS devices (K) were invisible.
   Now any non-SE0 attach triggers the reset sequence; PR in PORTSC also
   triggers it.
5. **Interrupt strobes** wired: USBINT on successful completion,
   USBERRINT on error, PCD on connect change.

---

## 9. Example & firmware (`examples/deca_ehci_host.py`, `examples/firmware/`)

### FIXED

- `firmware/ehci.h` register offsets now include the operational base
  (+0x20) and the corrected PORTSC layout; removed the invented
  `PORTSC_HSP` bit; fixed reversed wake-bit constants.
- `main.c` data-toggle bug: after a SETUP, the DATA stage now starts at
  DATA1 (USB 2.0 §8.5.3) instead of propagating the stale toggle.
- QH max-packet comment corrected (bits [26:16]).
- README register table corrected (CONFIGFLAG 0x60, PORTSC 0x64
  absolute).

### OPEN

- The firmware programs the DMA schedule (frame list, QH, qTDs) that the
  gateware cannot consume yet (see §6.1). It is structurally sound and
  will become useful once the DMA schedule engine lands.
- `LED_OUT_BASE`/`UART_BASE` are guessed addresses; read them from
  `csr.csv`/`csr.json` at build time instead.

---

## 10. Test suite (`liteusb/tests/test_ehci_*.py`)

Rewritten and extended: **70 tests, all passing** (full liteusb suite:
126 passed, no regressions).

| File | Tests | Covers |
|------|-------|--------|
| `test_ehci_token.py` | 15 | CRC5 vs bit-serial reference (sampled exhaustive + boundaries + wire captures), exact token bytes for SETUP/IN/OUT/PING/SOF, SOF timing/µframe/hold |
| `test_ehci_registers.py` | 33 | Capability block, all op registers, RW1C semantics, HCRESET/IAA self-clear, FRINDEX halted-write + running-follow, PORTSC per-port semantics, interrupt masking/generation, 2-port decode |
| `test_ehci_schedule.py` | 8 | HCHalted, FRINDEX per-SOF advance, PSS/ASS pulsing, stub issues no transfers |
| `test_ehci_transfer.py` | 8 | Full OUT/IN transaction flows through the real packet layer incl. CRC16 on the wire, NAK/STALL/timeout-retry, ZLP, SETUP DATA0 |
| `test_ehci_host.py` | 13 | SE0 reset drive, FS/LS/HS detection, host chirp order + terminations, LS/chirp disambiguation, TT tokens/handshakes/timeout/PHY restore |

Not covered (needs the DMA engine): QH/qTD traversal, iTD/siTD,
split transactions, isochronous scheduling, IAA doorbell flow,
interrupt-threshold moderation.
