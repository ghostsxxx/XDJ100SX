# XDJ100SX Network Sync — Design

Status: draft, decisions agreed 2026-10-16. Nothing implemented yet.

## Goal

Several XDJ100SX players (2–4, protocol unbounded) on one LAN, each a Raspberry
Pi running its own Mixxx with one deck, behave like Pioneer Pro DJ Link players:

1. **Beat monitor (v1):** every player shows how far its beat is ahead of or
   behind the reference player, in milliseconds, plus the BPM difference.
2. **Tempo sync (v2):** a player follows the reference player's BPM.
3. **Beat sync (v3):** a player follows tempo *and* phase, continuously, like
   Mixxx's local SYNC button — but against a player on another machine.

v1 changes nothing about playback, but its data model and clock handling must
already be good enough for v3. The end goal is full sync; v1 is the first slice.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Implement inside Mixxx (C++), on the fork `ghostsxxx/mixxx`, branch `xdj-sync`, based on upstream `2.6`. | Controller scripts have no network access, and Mixxx has no OSC/UDP output. 2.6 is what the XDJ100SX image uses. |
| D2 | Keep it a separate module (`src/network/xdjsync/` or similar) with a thin hook into `EngineSync`. | Cheap to rebase onto new Mixxx releases; possible to upstream later. |
| D3 | For v3, a remote player becomes a `Syncable` (like `InternalClock`), so Mixxx's existing sync engine does tempo and phase following. | Reuses tested sync logic; local SYNC/leader buttons keep working. |
| D4 | Clock alignment between players via our own ping/pong exchange in the protocol (NTP-style, four timestamps, min-RTT filtering, drift estimate). | Zero config; no dependency on chrony/NTP setup of the OS image. |
| D5 | Phase is reported at **output (DAC) time**, not engine buffer time: each player adds its own output latency. | Players with different buffer sizes would otherwise show 0 ms while being audibly apart. |
| D6 | UI: one large indicator against a single reference player, next to the BPM display. | Small 1024×600-ish screen; matches how Pioneer shows it. |
| D7 | ~~Reference = player in MASTER mode, else the only playing player.~~ Replaced by D18. | |
| D8 | Transport: everything is UDP broadcast (port 51760), versioned binary packets. A ping is answered by every peer with a pong addressed by player id. | Multicast is unreliable on cheap switches and Pi WiFi. Unicast reaches only one of several players on the same machine (SO_REUSEPORT). Binary keeps packets small at 50 Hz. |
| D9 | Feature off by default, enabled in preferences; player ID from preferences, default = hostname. | Upstream-friendly, no surprise network traffic. |
| D10 | LAN only, no authentication. | DJ booth network; accepted risk. |
| D11 | No half/double tempo matching (e.g. 174 vs 87 BPM). | The DJ keeps one consistent BPM analysis strategy across players. |
| D12 | "In sync" = offset ≤ 5 ms and not drifting. | Well below an audible flam (~10 ms), margin for display accuracy. |
| D14 | Tempo sync (v2): in BPM or BEAT mode the network thread sets `[Channel1],bpm` to the reference BPM whenever they differ by ≥ 0.0005 BPM; Mixxx turns it into a rate change. Not via Mixxx's sync engine. | Mixxx's sync only aligns phase with quantize on, so it can't separate tempo-only from beat sync. Revisit for v3. |
| D15 | While following, the pitch fader is overridden, and the tempo may go beyond the pitch range. | Same as Pioneer sync and Mixxx's own sync. |
| D16 | SYNC is one on/off toggle on the touchscreen (`[XdjSync],sync`, persistent); MASTER is a lit indicator (`[XdjSync],master`), not a button. No hardware mapping (all CDJ-100S buttons are in use). Sync type via config `[XdjSync] SyncType` = `beat` (default) or `bpm`. | Like Pioneer CDJs: press SYNC, master is automatic. The XDJ100SX screen is a touchscreen. |
| D17 | Beat sync (v3), BEAT mode, only while both play and clock uncertainty ≤ 5 ms: offset > 20 ms → fractional `beatjump` by −offset (≤ every 500 ms); 0.5–20 ms → tempo trim `ref × (1 − offset_s / 2 s)`, max ±0.5 %; < 0.5 ms → exact reference tempo. In-sync indicator ignores the trim's tempo difference. | Snap like Pioneer when engaging; smooth, inaudible correction after. Not via Mixxx's sync engine (D14). Beat jump inside an active loop moves the loop (Mixxx behavior). |
| D18 | Automatic master: the player (any mode) that has been playing longest with a known tempo. Start time on each player's clock = arrival time − `playingFor` (state packet field, appended). Starts within 100 ms count as simultaneous → lowest player id. Master stops or disappears → next-longest player takes over. | Every player computes the same master from shared state, no voting. Stable: changes only when the master stops; the new master already runs at the old master's tempo. |
| D19 | Bars: 4/4 always, counted from the beatgrid's first marker. Beat index sent with the state (flag bit 1 = valid). Offset reported as whole beats off in the bar plus the in-beat offset; BEAT sync aligns beat 1 on beat 1 by default (config `SyncAlign` = `bar`/`beat`). In-sync requires the same beat in the bar. | DJ cares about bars, not single beats (phrase mixing). |
| D20 | "BAR 1" touch button re-anchors the loaded track's grid by whole beats so the beat nearest the play position is bar beat 1; saved with the grid. Constant-tempo grids only. | Mixxx's analyzer doesn't detect downbeats (on the click track it anchored on the 2nd beat). |
| D21 | Cue countdown: bars.beat (CDJ-style: remaining beats rounded up, beat counts 4-3-2-1 within the bar, e.g. 64 beats = 15.4, last beat = 00.1) to the next **hot cue** ahead; own and the reference's (sent as beats at `atDac`, appended field, projected on the receiver). Main cue, intro/outro and sound markers are ignored. | Phrase mixing: see how far away the next drop is on both decks. Main cue etc. sit at the track start in Mixxx. |
| D22 | UI = CDJ-3000-style beat lanes (`<XdjBeatLanes>`, native widget `WXdjBeatLanes`): master lane on top, own lane below, one time axis around a shared playhead, beat ticks, numbered bars, red hot cue flags, fixed-width bars.beat countdown per lane; SYNC/MASTER right; BAR 1 next to Q. Replaces the separate bar meter, phase slider, ms readout and countdown row. Master sends up to 8 hot cues (16 beats behind to ahead) for its lane (appended field). | One picture for phase, beat-in-bar and tempo offset (ticks not lining up); numbers don't jump. Design: claude.ai artifact "XDJ100SX Sync Panel". |
| D13 | Skin: slim phase strip under the waveform: center-zero meter (right = ahead, edges = ±½ beat), green when in sync, orange otherwise, offset in ms. | Readable at a glance like Pioneer's phase meter; fits the 800×480 screen. |

## Non-functional requirements

| Area | Requirement |
|---|---|
| Audio safety | No network I/O, locks or allocations on the audio thread. The engine publishes a snapshot (lock-free); a separate thread sends/receives. Zero extra xruns on a Pi 3B+. |
| Resources | < 3 % CPU and < 10 MB RAM on a Pi 3B+. |
| Accuracy (Ethernet) | Displayed offset within ±1 ms of the audible offset (target), ±2 ms (must). Comparable to Pioneer. |
| Accuracy (WiFi) | Best effort, target ±5 ms. Display shows a quality indicator and greys out when clock alignment is not trustworthy. |
| Display | ≥ 20 Hz updates, smoothed, no visible jitter on a steady signal. |
| Robustness | Peer loss detected and shown within 1 s. Malformed/foreign/old-version packets ignored. Network problems never affect playback. |
| Scale | Protocol: any number of players. UI: one reference; tested with 4. |
| Zero config | Players find each other automatically on the same subnet. |
| Testability | Core math and protocol testable without network, audio or Mixxx running (fake clock). |

## How the offset is computed

Each player sends, about 50–100 times per second:

```
version, playerId, seq
playing, syncMode (FREE/BPM/BEAT/MASTER)
bpm            (current, incl. pitch)
beatDistance   (0..1, phase within the current beat)
beatIndex      (integer beat counter, for bar alignment later)
tDac           (sender clock, ns: when this phase is heard at the output)
```

The receiver converts `tDac` to its own clock with the estimated clock offset,
then projects the peer's phase to "now":

```
peerPhase(now) = beatDistance + (now - tDac_local) * bpm / 60
offsetBeats    = wrap(localPhase(now) - peerPhase(now))   into [-0.5, 0.5)
offsetMs       = offsetBeats * 60000 / localBpm
```

If the BPMs differ by more than a small threshold, the offset drifts, so the UI
shows the BPM difference (and drift direction) rather than a stable ms value.

Clock alignment: ping each peer every 250 ms, keep the last 8 samples (2 s) and
use the one with the lowest round trip; its half round trip is the worst-case
error, exposed so the UI can grey out. Over 2 s crystal drift (< 100 ppm) stays
below 0.2 ms, so no drift model in v1.

## Implementation status (mixxx fork, `src/xdjsync/`)

| Part | File | Status |
|---|---|---|
| Phase/offset math | `beatphase.*` | done, tested |
| Packet codec (State/Ping/Pong, v1, ~52 B state) | `packet.*` | done, tested |
| Clock offset estimator | `clockestimator.*` | done, tested |
| Peer table, reference choice (D7), timeout | `peertable.*` | done, tested |
| UDP transport on own thread, `[XdjSync]` controls | `xdjsyncservice.*` | done, verified against simulator |
| Engine phase snapshot at DAC time (D5) | `enginephase.*`, hooks in `sounddeviceportaudio.cpp`, `enginemixer.cpp` | done, tested; playing on one Mac: offset jitter 0.01–0.02 ms std dev, consistent with independent monitor (±0.03 ms). Audible accuracy not yet measured. |
| Automatic master + SYNC toggle (D16, D18) | `peertable.*`, skin `SyncButton`/`MasterIndicator` | done, tested; verified handover between two simulated players |
| Tempo sync (v2, BPM mode) | `tempofollow.*` | done, tested; follows master tempo changes (verified 128 → 130.5 → 124 → 150) |
| Beat sync (v3, BEAT mode) | `phasefollower.*` | done, tested; verified vs simulator: −175 ms snap → 0 in < 1 s; 10 ms trimmed with ~2 s time constant to ≤ 0.5 ms; 60 ms snap → −0.02 ms |
| Audible accuracy | `sync/devenv/measure_audible.sh` | measured on the Mac (2 Mixxx → BlackHole, 48 kHz, 512-frame buffers): one player in BEAT following the other, audible offset −0.38 ms median (worst 1.0 ms) while the display shows −0.02 ms |
| Beat lanes UI (D22) | `wxdjbeatlanes.*`, `lanegeometry.*`, skin `phasemeter.xml` | done, tested (geometry, codec, render-to-PNG); live data verified vs simulator |
| Bars + cue countdown (D19–D21) | `beatphase.*`, `countdown.*`, `gridposition.*`, skin bar meter / countdown row / BAR 1 | done, tested; verified vs simulator (cue every 16 bars) |
| Skin phase strip | XDJ100SX `phasemeter.xml`, `images/phase/` | done, checked visually; mode button added |

Note: Mixxx compiles with `-ffast-math`; use `util_isfinite`/`util_isnan`
(`util/fpclassify.h`), never `std::isfinite`, and don't build NaN/Inf
constants in code or tests.

## Mixxx integration points (2.6)

- `src/engine/sync/syncable.h`, `enginesync.*`, `internalclock.*`: sync engine;
  `InternalClock` is the model for a non-deck `Syncable` (D3).
- `EngineSync::onCallbackStart/End`: per audio callback, the natural place to
  take the phase snapshot.
- `src/soundio/sounddeviceportaudio.cpp`: knows stream output latency (D5);
  PortAudio's per-callback `timeInfo` is currently unused and could give exact
  DAC times.
- `[XdjSync]` controls: `enabled`, `sync` (toggle, persistent), `master`, `peer_count`, `ref_present`, `ref_offset_valid`,
  `ref_offset_ms`, `ref_phase` (0..1, 0.5 = aligned), `ref_in_sync`, `ref_bpm`,
  `ref_bpm_delta`, `ref_drifting`, `ref_clock_uncertainty_ms`. Always present;
  networking only runs if enabled.
- XDJ100SX skin (`mixxx/SKIN/XDJ100SX/deck.xml`): indicator next to `DeckBPM`.

## Phase timing (D5)

- Audio callback entry (clock reference device): the driver's own
  "callback → DAC" delay (PortAudio `outputBufferDacTime - currentTime`) gives
  when the buffer's first sample is heard; Mixxx's waveform estimate only as
  fallback when that is implausible (≤ 0 or ≥ 1 s). Mixxx's estimate rejects
  driver timestamps on CPU-timer jitter (seen with CoreAudio/BlackHole) and
  then guesses within two buffers, differently per process: measured 18.9 ms
  display error before this fix.
- `beat_distance` is updated in `EngineBuffer::postProcess()` for the play
  position after the rendered buffer, so it is heard one buffer later:
  `atDac = firstSampleAtDac + bufferDuration`.
- After `EngineMixer::processChannels()` Channel1's play/bpm/beat_distance are
  published lock-free (`ControlValueAtomic`); the network thread reads the
  latest. Older than 500 ms (audio stopped) → fallback to controls sampled
  on the network thread.
- `XdjSyncService` is deleted after `SoundManager` (no audio callbacks left).

## Test harness (on the Mac, no hardware)

Lives in this repo under `sync/`:

| Path | What |
|---|---|
| `sync/devenv/build-mixxx.sh` | Builds the Mixxx fork with unit tests. |
| `sync/devenv/players.sh` | Starts N virtual players: separate Mixxx instances with their own settings folder, XDJ100SX skin, test library. |
| `sync/testdata/` | Generated click tracks with exactly known BPM and first-beat position (ground truth). |
| `sync/devenv/set_cfg.py` | Sets keys in a player's `mixxx.cfg` (used by `players.sh`). |
| `sync/devenv/measure_audible.sh` | Records both players via a loopback device (BlackHole) and compares the audible click offset with the displayed one. |
| `sync/testdata/record_device.swift` | Gap-free recording of an input device (ffmpeg's avfoundation input dropped ~11 % of the audio). |
| `sync/testdata/measure_offset.py` | Click offset between the high and low click tracks by matched filtering; `--selftest` (error ≤ 0.06 ms). |
| `sync/sim/xdjsim.py` | `players`: virtual players (BPM, phase, mode, clock skew, jitter, loss, paused). `monitor`: independent view of all players and offsets. `selftest`. |

Test levels:

1. Unit tests (GTest, fake clock): phase math, wrap-around, packet codec, clock
   estimation, peer timeout, reference selection.
2. Simulator against one real Mixxx: known injected offsets must be displayed.
3. Two/four Mixxx instances on the Mac: same clock, so displayed offset can be
   checked exactly against nudges.
4. Network impairment: `dnctl`/`pfctl` (Mac), `tc netem` (Pi).
5. Ground truth on hardware: record two Pi outputs into one stereo interface,
   measure onset offset, compare with display.
6. Pi performance: xruns and CPU with feature on/off.

## Robustness (scenario runs on the Mac)

`HARNESS=1 sync/devenv/players.sh 1` + `sync/sim/scenarios.py`: player1 in
BEAT sync (driven by a virtual MIDI device with the XDJ100SX mapping) follows
an in-process simulated master; offset measured against the master's true
phase.

| Scenario | worst | at end | beat jumps |
|---|---|---|---|
| steady | 0.07 ms | 0.04 ms | – |
| master tempo ramp 128→132→128 | 1.0 ms | 0.5 ms | – |
| master tempo jump ±3 BPM | 1.2 ms | 0.5 ms | – |
| master nudge 8 ms | 8.6 ms | 0.9 ms | – (trim) |
| master nudge 40 ms | 40.5 ms | 0.02 ms | 1 (snap) |
| network drop 0.5 s | 0.03 ms | 0.02 ms | – |
| drop 3 s, master changes tempo meanwhile | 32 ms | 0.5 ms | 1 on reconnect |
| drop 5 s, reconnect 25 ms off | 25.5 ms | 0.1 ms | 1 on reconnect |
| 8 ms jitter, 20 % loss | 0.6 ms | 0.4 ms | – |

Wrong beat in the bar: never. During a drop longer than 1 s the follower
elects itself master (still playing at the last tempo); on reconnect the
longest-playing player wins again and the follower snaps back.

## Backlog

- Choose which cue the countdown counts to (not always the next one).
- Rekordbox cue/grid compatibility (memory cues, downbeats from Rekordbox
  analysis).

## Open questions


- Packet rate: 50 or 100 Hz on a Pi 3B+ over WiFi?
- Pitch-bend/jog nudges: how fast must the indicator react vs. how much to smooth?
- Bar (downbeat) alignment, like Pioneer: needs reliable beatgrid downbeats; later.
