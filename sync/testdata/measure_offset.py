#!/usr/bin/env python3
"""Measures the audible beat offset between two click tracks in one recording.

The recording contains the high click track (player A, 1500/2250 Hz) and the
low one (player B, 800/1200 Hz), e.g. both players' output recorded from a
loopback device. Clicks are found by matched filtering with the known click
waveforms (see make_clicks.py); the four tones are nearly orthogonal, so
overlapping clicks of both players don't disturb each other. Every A click is
paired with the nearest B click.

Prints the offset of A against B: positive = A's beat comes first (A ahead),
same sign as [XdjSync],ref_offset_ms on player A with B as reference.

Usage: measure_offset.py RECORDING.wav [--bpm 128]
       measure_offset.py --selftest
"""
import argparse
import sys
import wave

import numpy as np
from scipy import signal

CLICK_S = 0.012          # as in make_clicks.py
PLAYER_A_HZ = (1500.0, 2250.0)  # beat, downbeat
PLAYER_B_HZ = (800.0, 1200.0)


def read_wav(path):
    with wave.open(path, "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype={2: np.int16, 4: np.int32}[width])
    data = data.reshape(-1, channels).astype(np.float64).mean(axis=1)
    return data / np.max(np.abs(data) + 1e-12), rate


def template(freq, rate):
    n = int(CLICK_S * rate)
    i = np.arange(n)
    return np.exp(-i / (n / 5)) * np.sin(2 * np.pi * freq * i / rate)


def onsets(x, rate, freqs, beat_s):
    """Click start times in seconds for one player (beat and downbeat tones)."""
    times = []
    for freq in freqs:
        t = template(freq, rate)
        corr = signal.fftconvolve(x, t[::-1], mode="valid")  # corr[k]: click starting at k
        peaks, _ = signal.find_peaks(corr, height=0.4 * corr.max(), distance=int(0.5 * beat_s * rate))
        for k in peaks:
            # Sub-sample peak by parabolic interpolation.
            if 0 < k < len(corr) - 1:
                y0, y1, y2 = corr[k - 1], corr[k], corr[k + 1]
                k = k + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
            times.append(k / rate)
    return np.sort(np.array(times))


def measure(x, rate, bpm):
    beat_s = 60.0 / bpm
    a = onsets(x, rate, PLAYER_A_HZ, beat_s)
    b = onsets(x, rate, PLAYER_B_HZ, beat_s)
    if len(a) < 4 or len(b) < 4:
        raise SystemExit(f"too few clicks found (A {len(a)}, B {len(b)})")
    diffs = []
    for t in a:
        d = b[np.argmin(np.abs(b - t))] - t  # B later than A -> A ahead -> positive
        d = (d + beat_s / 2) % beat_s - beat_s / 2
        diffs.append(d * 1000.0)
    return np.array(diffs), len(a), len(b)


def report(diffs, n_a, n_b):
    print(f"clicks A {n_a}, B {n_b}, pairs {len(diffs)}")
    print(f"A vs B: median {np.median(diffs):+.3f} ms, mean {diffs.mean():+.3f} ms, "
          f"std {diffs.std():.3f} ms, min {diffs.min():+.3f}, max {diffs.max():+.3f}")
    return float(np.median(diffs))


def selftest():
    """Mix the two click tracks with a known shift plus noise, measure."""
    from pathlib import Path
    tracks = Path(__file__).parent / "tracks"
    hi, rate = read_wav(str(tracks / "click_128_00_high.wav"))
    lo, _ = read_wav(str(tracks / "click_128_00_low.wav"))
    ok = True
    for shift_ms in (-37.3, 0.0, 4.2, 12.7):
        n = int(round(abs(shift_ms) / 1000.0 * rate))
        # Positive shift: A ahead -> delay B.
        lo_s = np.concatenate([np.zeros(n), lo])[:len(lo)] if shift_ms > 0 else lo
        hi_s = np.concatenate([np.zeros(n), hi])[:len(hi)] if shift_ms < 0 else hi
        mix = hi_s[: rate * 10] + lo_s[: rate * 10] + 0.02 * np.random.default_rng(1).standard_normal(rate * 10)
        diffs, _, _ = measure(mix, rate, 128.0)
        got = float(np.median(diffs))
        expected = round(shift_ms / 1000.0 * rate) / rate * 1000.0
        good = abs(got - expected) < 0.1
        ok &= good
        print(f"shift {shift_ms:+6.1f} ms -> measured {got:+7.3f} ms {'OK' if good else 'FAILED'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", nargs="?")
    ap.add_argument("--bpm", type=float, default=128.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not args.recording:
        ap.error("recording required")
    x, rate = read_wav(args.recording)
    report(*measure(x, rate, args.bpm))


if __name__ == "__main__":
    main()
