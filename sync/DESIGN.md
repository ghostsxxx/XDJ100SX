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
| D7 | Reference player = the player in MASTER mode. If none is master and exactly one other player is playing, that one. Otherwise no indicator. | Unambiguous; same mental model as Pioneer. |
| D8 | Transport: everything is UDP broadcast (port 51760), versioned binary packets. A ping is answered by every peer with a pong addressed by player id. | Multicast is unreliable on cheap switches and Pi WiFi. Unicast reaches only one of several players on the same machine (SO_REUSEPORT). Binary keeps packets small at 50 Hz. |
| D9 | Feature off by default, enabled in preferences; player ID from preferences, default = hostname. | Upstream-friendly, no surprise network traffic. |
| D10 | LAN only, no authentication. | DJ booth network; accepted risk. |
| D11 | No half/double tempo matching (e.g. 174 vs 87 BPM). | The DJ keeps one consistent BPM analysis strategy across players. |
| D12 | "In sync" = offset ≤ 5 ms and not drifting. | Well below an audible flam (~10 ms), margin for display accuracy. |
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
| Engine snapshot at DAC time | | todo: phase sampled on the network thread for now |
| Skin phase strip | XDJ100SX `phasemeter.xml`, `images/phase/` | done, not yet checked visually |

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
- `[XdjSync]` controls: `enabled`, `sync_mode` (0 free, 1 bpm, 2 beat,
  3 master; settable), `peer_count`, `ref_present`, `ref_offset_valid`,
  `ref_offset_ms`, `ref_phase` (0..1, 0.5 = aligned), `ref_in_sync`, `ref_bpm`,
  `ref_bpm_delta`, `ref_drifting`, `ref_clock_uncertainty_ms`. Always present;
  networking only runs if enabled.
- XDJ100SX skin (`mixxx/SKIN/XDJ100SX/deck.xml`): indicator next to `DeckBPM`.

## Test harness (on the Mac, no hardware)

Lives in this repo under `sync/`:

| Path | What |
|---|---|
| `sync/devenv/build-mixxx.sh` | Builds the Mixxx fork with unit tests. |
| `sync/devenv/players.sh` | Starts N virtual players: separate Mixxx instances with their own settings folder, XDJ100SX skin, test library. |
| `sync/testdata/` | Generated click tracks with exactly known BPM and first-beat position (ground truth). |
| `sync/devenv/set_cfg.py` | Sets keys in a player's `mixxx.cfg` (used by `players.sh`). |
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

## Open questions


- Packet rate: 50 or 100 Hz on a Pi 3B+ over WiFi?
- Pitch-bend/jog nudges: how fast must the indicator react vs. how much to smooth?
- Bar (downbeat) alignment, like Pioneer: needs reliable beatgrid downbeats; later.
