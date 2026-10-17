#!/usr/bin/env python3
"""Robustness scenarios: a simulated master misbehaves, Mixxx player1 follows.

Needs player1 running with SYNC on (BEAT) and the MIDI harness:
    HARNESS=1 sync/devenv/players.sh 1
No other master may be on the network (stop other sims).

The master runs in this process on the same clock as the measurement, so
player1's real offset is known even while the master's packets are dropped.
player1's phase comes from its own state packets (clock aligned by ping/pong).

Prints per phase: worst and final offset within the beat, time spent on the
wrong beat of the bar, and the sync beat jumps player1 logged.

Usage: scenarios.py [--only NAME ...] [--players-dir DIR]
"""
import argparse
import math
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xdjsim  # noqa: E402

PLAYER = "player1"
MASTER = "simmaster"


class Master:
    """A master whose tempo and phase change continuously or in steps."""

    def __init__(self, bpm):
        self.bpm = bpm
        self.anchor_ns = xdjsim.now_ns()
        self.anchor_beat = 400.0  # some way into the track
        self.sending = True
        self.jitter_ms = 0.0
        self.loss = 0.0
        self.seq = 0
        self.started_ns = self.anchor_ns - int(600e9)  # "playing for 10 min"

    def beats(self, t_ns):
        return self.anchor_beat + (t_ns - self.anchor_ns) / 1e9 * self.bpm / 60.0

    def set_bpm(self, bpm):
        t = xdjsim.now_ns()
        self.anchor_beat = self.beats(t)
        self.anchor_ns = t
        self.bpm = bpm

    def shift_ms(self, ms):
        """Positive: the master's beats come later (it falls behind)."""
        self.anchor_beat -= ms / (60000.0 / self.bpm)

    def state(self):
        t = xdjsim.now_ns()
        b = self.beats(t)
        self.seq += 1
        return xdjsim.encode_state(MASTER, self.seq, True, 0, self.bpm, b - math.floor(b),
                                   math.floor(b), t, t - self.started_ns, True)


class Run:
    def __init__(self, players_dir):
        self.sock = xdjsim.make_socket(xdjsim.PORT)
        self.sched = xdjsim.Scheduler(self.sock)
        self.mon = xdjsim.Monitor(self.sock, xdjsim.PORT, xdjsim.BROADCAST, "scenario")
        self.master = Master(128.0)
        self.samples = []  # (t, offset_ms within beat, whole beats off in bar)
        self.players_dir = Path(players_dir)

    # --- network ---

    def send(self, data):
        if random.random() < self.master.loss:
            return
        delay = random.uniform(0, self.master.jitter_ms / 1000.0)
        if delay:
            self.sched.at(time.monotonic() + delay,
                          lambda: self.sock.sendto(data, (xdjsim.BROADCAST, xdjsim.PORT)))
        else:
            self.sock.sendto(data, (xdjsim.BROADCAST, xdjsim.PORT))

    def on_packet(self, packet, received_ns):
        self.mon.on_packet(packet, received_ns)
        ptype, f = packet
        if ptype == xdjsim.T_PING and f["id"] != MASTER and self.master.sending:
            t2 = received_ns
            self.send(xdjsim.encode_pong(MASTER, f["id"], f["seq"], f["sent"], t2, xdjsim.now_ns()))

    def tick_state(self):
        if self.master.sending:
            self.send(self.master.state())
        self.sched.at(time.monotonic() + xdjsim.STATE_INTERVAL, self.tick_state)

    def tick_ping(self):
        self.mon.ping()
        self.sched.at(time.monotonic() + xdjsim.PING_INTERVAL, self.tick_ping)

    def tick_sample(self):
        sample = self.measure()
        if sample:
            self.samples.append(sample)
        self.sched.at(time.monotonic() + 0.05, self.tick_sample)

    def measure(self):
        """player1 vs the master's true phase, now."""
        if PLAYER not in self.mon.states:
            return None
        state, _ = self.mon.states[PLAYER]
        est = self.mon.clocks.get(PLAYER, xdjsim.Clock()).estimate()
        if est is None or not state["playing"] or state["bpm"] <= 0:
            return None
        t = xdjsim.now_ns()
        at_local = state["at_dac"] - est[0]
        own = state["beat_distance"] + (t - at_local) / 1e9 * state["bpm"] / 60.0
        master = self.master.beats(t)
        beat_offset = xdjsim.wrap_half(own - (master - math.floor(master)))
        ms = beat_offset * 60000.0 / state["bpm"]
        beats_off = 0
        if state["beat_index_valid"]:
            own_bar = (state["beat_index"] % 4) + own
            bar_offset = own_bar - (master % 4)
            bar_offset -= 4 * math.floor((bar_offset + 2) / 4)
            beats_off = round(bar_offset - beat_offset)
        return time.monotonic(), ms, beats_off

    # --- control ---

    def wait(self, seconds):
        self.sched.run(self.on_packet, until=time.monotonic() + seconds)

    def midi(self, line):
        fifo = self.players_dir / PLAYER / "midi.fifo"
        with open(fifo, "w") as f:
            f.write(line + "\n")

    def ensure_playing(self):
        self.wait(1.5)
        state = self.mon.states.get(PLAYER, ({"playing": False},))[0]
        if not state["playing"]:
            self.midi("90 3C 7F\n80 3C 00")
            self.wait(1.0)

    def ramp(self, to_bpm, seconds):
        steps = max(1, int(seconds / 0.02))
        start = self.master.bpm
        for i in range(1, steps + 1):
            self.master.set_bpm(start + (to_bpm - start) * i / steps)
            self.wait(0.02)


def beat_jumps(log_path, since, until):
    """'sync beat jump' lines of player1 between two wall-clock datetimes."""
    jumps = []
    pattern = re.compile(r"^(\d\d:\d\d:\d\d\.\d\d\d) .*sync beat jump ([-0-9.e]+)")
    day = since.date()
    with open(log_path, errors="replace") as f:
        for line in f:
            m = pattern.match(line)
            if not m:
                continue
            t = datetime.combine(day, datetime.strptime(m.group(1), "%H:%M:%S.%f").time())
            if since <= t <= until:
                jumps.append(float(m.group(2)))
    return jumps


def summarize(run, name, t0, t1, wall0, wall1):
    window = [(ms, off) for (t, ms, off) in run.samples if t0 <= t <= t1]
    jumps = beat_jumps(run.players_dir / PLAYER / "mixxx.log", wall0, wall1)
    if not window:
        print(f"{name:<28} no samples")
        return
    worst = max(abs(ms) for ms, _ in window)
    tail = [ms for (t, ms, _) in run.samples if t1 - 1.0 <= t <= t1]
    final = sum(abs(ms) for ms in tail) / len(tail) if tail else float("nan")
    wrong_bar = sum(1 for _, off in window if off != 0) / len(window) * 100
    jump_text = ", ".join(f"{j:+.2f}" for j in jumps) if jumps else "-"
    print(f"{name:<28} worst {worst:7.2f} ms   end {final:6.2f} ms   "
          f"wrong beat {wrong_bar:5.1f} %   jumps {jump_text}")


SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


@scenario
def steady(run):
    run.wait(8)


@scenario
def tempo_ramp(run):
    run.ramp(132.0, 4.0)
    run.wait(4)
    run.ramp(128.0, 4.0)
    run.wait(3)


@scenario
def tempo_jump(run):
    run.master.set_bpm(131.0)
    run.wait(5)
    run.master.set_bpm(128.0)
    run.wait(5)


@scenario
def nudge_small(run):
    run.master.shift_ms(8)
    run.wait(5)


@scenario
def nudge_large(run):
    run.master.shift_ms(40)
    run.wait(5)


@scenario
def drop_short(run):
    run.master.sending = False
    run.wait(0.5)
    run.master.sending = True
    run.wait(3)


@scenario
def drop_with_tempo_change(run):
    run.master.sending = False
    run.wait(1.0)
    run.master.set_bpm(130.0)
    run.wait(2.0)
    run.master.sending = True
    run.wait(5)
    run.master.set_bpm(128.0)
    run.wait(3)


@scenario
def drop_long_reconnect(run):
    run.master.sending = False
    run.wait(5.0)
    run.master.shift_ms(25)
    run.master.sending = True
    run.wait(5)


@scenario
def wifi_jitter_loss(run):
    run.master.jitter_ms = 8.0
    run.master.loss = 0.2
    run.wait(10)
    run.master.jitter_ms = 0.0
    run.master.loss = 0.0
    run.wait(2)


@scenario
def master_beat_jump(run):
    """Quantized beat jump on the master: 6 beats, so the bar is off by 2."""
    run.master.anchor_beat += 6
    run.wait(4)


@scenario
def master_hot_cue(run):
    """Non-quantized hot cue on the master: 6.37 beats."""
    run.master.anchor_beat += 6.37
    run.wait(4)


@scenario
def master_new_track(run):
    """The master loads another track and plays it from its start."""
    run.master.anchor_ns = xdjsim.now_ns()
    run.master.anchor_beat = -0.6  # just before its first downbeat
    run.wait(5)


@scenario
def reconnect_bar_off(run):
    """During a 3 s drop the master beat-jumps one beat."""
    run.master.sending = False
    run.wait(1.0)
    run.master.anchor_beat += 1
    run.wait(2.0)
    run.master.sending = True
    run.wait(5)


@scenario
def follower_nudge(run):
    """The DJ nudges player1 with the jog wheel: the new offset must stay."""
    for _ in range(3):
        run.midi("B1 14 44")  # jog +4, a light touch
        run.wait(0.02)
    run.wait(6)


def player_bpm(run):
    state = run.mon.states.get(PLAYER)
    return state[0]["bpm"] if state else None


def set_fader(run, rate):
    """Pitch fader to Mixxx rate -1..1 (14-bit, as the XDJ100SX sends it)."""
    full = max(0, min(16383, round(8192 - rate * 8192)))
    run.midi(f"B0 00 {full >> 7:02X}\nB0 20 {full & 0x7F:02X}")


def bpm_trace(run, seconds, action=None):
    """Samples player1's BPM every 50 ms while running action(step)."""
    trace = []
    steps = int(seconds / 0.05)
    for i in range(steps):
        if action:
            action(i, steps)
        run.wait(0.05)
        trace.append(player_bpm(run))
    return [b for b in trace if b is not None]


def describe(name, trace):
    steps = [abs(b - a) for a, b in zip(trace, trace[1:])]
    print(f"  {name:<34} BPM {min(trace):7.3f} .. {max(trace):7.3f}   "
          f"largest step {max(steps) if steps else 0:6.3f}")


@scenario
def pitch_takeover(run):
    """Pitch fader: ignored while following, soft takeover after SYNC off.

    The fader moves like a real one (through every position); the BPM trace
    is continuous across phases, so a jump between phases shows too."""
    run.master.set_bpm(131.0)
    set_fader(run, 0.0)
    run.wait(3)
    trace = [player_bpm(run)]

    def phase(name, seconds, action=None):
        part = bpm_trace(run, seconds, action)
        describe(name, [trace[-1]] + part)
        trace.extend(part)

    phase("synced, fader 0 -> +0.5", 2.0, lambda i, n: set_fader(run, 0.5 * (i + 1) / n))
    run.midi("90 4A 7F\n80 4A 00")  # SYNC off
    phase("SYNC off, fader untouched", 2.0)
    phase("fader +0.5 -> -1 (takeover)", 6.0, lambda i, n: set_fader(run, 0.5 - 1.5 * (i + 1) / n))
    phase("fader -1 -> +1", 6.0, lambda i, n: set_fader(run, -1.0 + 2.0 * (i + 1) / n))
    run.midi("90 4A 7F\n80 4A 00")  # SYNC on
    phase("SYNC on again", 3.0)
    run.master.set_bpm(128.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--players-dir", default=str(Path(__file__).parents[3] / ".xdj-players"))
    args = ap.parse_args()
    run = Run(args.players_dir)
    now = time.monotonic()
    run.sched.at(now, run.tick_state)
    run.sched.at(now, run.tick_ping)
    run.sched.at(now, run.tick_sample)
    run.ensure_playing()
    print("settling ...", flush=True)
    run.wait(6)
    names = args.only or list(SCENARIOS)
    for name in names:
        t0, wall0 = time.monotonic(), datetime.now()
        SCENARIOS[name](run)
        summarize(run, name, t0, time.monotonic(), wall0, datetime.now())
        sys.stdout.flush()
        run.wait(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
