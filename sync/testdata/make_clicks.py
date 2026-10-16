#!/usr/bin/env python3
"""Generate click tracks with exactly known BPM and first-beat position.

These are the ground truth for sync tests: every beat is a short click, bar
downbeats are accented. Use different click pitches for different players so
flams are easy to hear when two players are mixed.

Usage: make_clicks.py [OUT_DIR]   (default: ./tracks next to this script)
"""
import math
import struct
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 44100
DURATION_S = 180

# name, bpm, first beat offset in seconds, click frequency in Hz
TRACKS = [
    ("click_174_00_high", 174.0, 0.0, 1500.0),
    ("click_174_00_low", 174.0, 0.0, 800.0),
    ("click_174_00_low_offset40ms", 174.0, 0.040, 800.0),
    ("click_128_00_high", 128.0, 0.0, 1500.0),
    ("click_128_00_low", 128.0, 0.0, 800.0),
    ("click_128_50_low", 128.5, 0.0, 800.0),
]

CLICK_S = 0.012


def render(bpm, first_beat_s, freq):
    n = SAMPLE_RATE * DURATION_S
    samples = [0.0] * n
    beat_s = 60.0 / bpm
    click_len = int(CLICK_S * SAMPLE_RATE)
    beat = 0
    while True:
        start = int(round((first_beat_s + beat * beat_s) * SAMPLE_RATE))
        if start >= n:
            break
        downbeat = beat % 4 == 0
        amp = 0.9 if downbeat else 0.5
        f = freq * 1.5 if downbeat else freq
        for i in range(min(click_len, n - start)):
            env = math.exp(-i / (click_len / 5))
            samples[start + i] = amp * env * math.sin(2 * math.pi * f * i / SAMPLE_RATE)
        beat += 1
    return samples


def write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s)) * 32767)
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "tracks"
    out.mkdir(parents=True, exist_ok=True)
    for name, bpm, first, freq in TRACKS:
        path = out / f"{name}.wav"
        if path.exists():
            continue
        write_wav(path, render(bpm, first, freq))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
