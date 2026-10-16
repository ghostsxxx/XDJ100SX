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
| D8 | Transport: UDP broadcast for state/discovery, UDP unicast for clock ping/pong. Versioned binary packet. | Multicast is unreliable on cheap switches and Pi WiFi. Binary keeps packets small at 50–100 Hz. |
| D9 | Feature off by default, enabled in preferences; player ID from preferences, default = hostname. | Upstream-friendly, no surprise network traffic. |
| D10 | LAN only, no authentication. | DJ booth network; accepted risk. |

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

Clock alignment: periodic ping/pong per peer, keep the samples with the lowest
round-trip time, estimate offset and drift. Exposed as a quality value so the UI
can grey out.

## Mixxx integration points (2.6)

- `src/engine/sync/syncable.h`, `enginesync.*`, `internalclock.*`: sync engine;
  `InternalClock` is the model for a non-deck `Syncable` (D3).
- `EngineSync::onCallbackStart/End`: per audio callback, the natural place to
  take the phase snapshot.
- `src/soundio/sounddeviceportaudio.cpp`: knows stream output latency (D5);
  PortAudio's per-callback `timeInfo` is currently unused and could give exact
  DAC times.
- New controls for the skin, e.g. `[XdjSync],ref_offset_ms`, `ref_bpm_delta`,
  `ref_present`, `ref_quality`, `sync_mode`.
- XDJ100SX skin (`mixxx/SKIN/XDJ100SX/deck.xml`): indicator next to `DeckBPM`.

## Test harness (on the Mac, no hardware)

Lives in this repo under `sync/`:

| Path | What |
|---|---|
| `sync/devenv/build-mixxx.sh` | Builds the Mixxx fork with unit tests. |
| `sync/devenv/players.sh` | Starts N virtual players: separate Mixxx instances with their own settings folder, XDJ100SX skin, test library. |
| `sync/testdata/` | Generated click tracks with exactly known BPM and first-beat position (ground truth). |
| `sync/sim/` (later) | Python peer simulator (BPM, phase, jitter, loss, clock skew/drift) and packet sniffer. |

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
