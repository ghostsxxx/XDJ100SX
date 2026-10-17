#!/usr/bin/env python3
"""XDJ sync peer simulator and monitor (protocol v1, see sync/DESIGN.md).

  players   virtual players that broadcast state and answer pings
  monitor   pings everyone, estimates each player's clock and prints the beat
            offset of every player against every other one, independently of
            the Mixxx implementation
  selftest  runs two virtual players and a monitor in-process, checks offsets

Examples:
  xdjsim.py players --player sim2:174 --player sim3:174:40:master
  xdjsim.py players --player sim2:128.5 --skew-ms 3000 --jitter-ms 5 --loss 0.1
  xdjsim.py monitor
  xdjsim.py selftest

Player spec: ID:BPM[:PHASE_MS[:MODE]], PHASE_MS shifts the beats later
(behind), MODE is free|bpm|beat|master (informational; the master is the
longest-playing player, see --playing-for-s).
"""
import argparse
import heapq
import math
import random
import select
import socket
import struct
import sys
import time

PORT = 51760
BROADCAST = "255.255.255.255"
MAGIC = b"XDJS"
VERSION = 1
T_STATE, T_PING, T_PONG = 1, 2, 3
MODES = {"free": 0, "bpm": 1, "beat": 2, "master": 3}
STATE_INTERVAL = 0.020
PING_INTERVAL = 0.250
CLOCK_WINDOW = 8
PEER_TIMEOUT = 1.0


# --- codec -------------------------------------------------------------------

def _id(player_id):
    raw = player_id.encode()
    return struct.pack("<B", len(raw)) + raw


def encode_state(pid, seq, playing, mode, bpm, beat_distance, beat_index, at_dac_ns,
                 playing_for_ns=0):
    return (MAGIC + struct.pack("<BB", VERSION, T_STATE) + _id(pid) +
            struct.pack("<IBBddqqq", seq, 1 if playing else 0, mode, bpm,
                        beat_distance, beat_index, at_dac_ns, playing_for_ns))


def encode_ping(pid, seq, sent_ns):
    return MAGIC + struct.pack("<BB", VERSION, T_PING) + _id(pid) + struct.pack("<Iq", seq, sent_ns)


def encode_pong(pid, to_pid, seq, t1, t2, t3):
    return (MAGIC + struct.pack("<BB", VERSION, T_PONG) + _id(pid) + _id(to_pid) +
            struct.pack("<Iqqq", seq, t1, t2, t3))


def decode(data):
    """Returns (type, dict) or None."""
    try:
        if data[:4] != MAGIC:
            return None
        version, ptype = struct.unpack_from("<BB", data, 4)
        if version != VERSION:
            return None
        off = 6

        def read_id():
            nonlocal off
            (n,) = struct.unpack_from("<B", data, off)
            if n == 0 or n > 32:
                raise ValueError("bad id")
            s = data[off + 1:off + 1 + n]
            if len(s) != n:
                raise ValueError("short id")
            off += 1 + n
            return s.decode()

        pid = read_id()
        if ptype == T_STATE:
            seq, flags, mode, bpm, bd, bi, at = struct.unpack_from("<IBBddqq", data, off)
            off += struct.calcsize("<IBBddqq")
            playing_for = struct.unpack_from("<q", data, off)[0] if len(data) >= off + 8 else 0
            if not (math.isfinite(bpm) and bpm >= 0 and math.isfinite(bd)) or mode > 3:
                return None
            return T_STATE, dict(id=pid, seq=seq, playing=bool(flags & 1), mode=mode,
                                 bpm=bpm, beat_distance=bd, beat_index=bi, at_dac=at,
                                 playing_for=playing_for)
        if ptype == T_PING:
            seq, sent = struct.unpack_from("<Iq", data, off)
            return T_PING, dict(id=pid, seq=seq, sent=sent)
        if ptype == T_PONG:
            to = read_id()
            seq, t1, t2, t3 = struct.unpack_from("<Iqqq", data, off)
            return T_PONG, dict(id=pid, to=to, seq=seq, t1=t1, t2=t2, t3=t3)
    except (struct.error, ValueError, UnicodeDecodeError):
        return None
    return None


# --- shared pieces -----------------------------------------------------------

def make_socket(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("0.0.0.0", port))
    s.setblocking(False)
    return s


def now_ns():
    return time.monotonic_ns()


def wrap_half(beats):
    return beats - math.floor(beats + 0.5)


class Clock:
    """Same estimator as Mixxx: min round trip over the last 8 samples."""

    def __init__(self):
        self.samples = []

    def add(self, t1, t2, t3, t4):
        rtt = (t4 - t1) - (t3 - t2)
        if t4 < t1 or t3 < t2 or rtt < 0:
            return
        self.samples = (self.samples + [(rtt, ((t2 - t1) + (t3 - t4)) // 2)])[-CLOCK_WINDOW:]

    def estimate(self):
        """(offset_ns remote-local, rtt_ns) or None."""
        if len(self.samples) < 3:
            return None
        rtt, offset = min(self.samples)
        return offset, rtt


class Scheduler:
    """Tiny event loop: timers plus one socket."""

    def __init__(self, sock):
        self.sock = sock
        self.events = []
        self.counter = 0

    def at(self, when, fn):
        self.counter += 1
        heapq.heappush(self.events, (when, self.counter, fn))

    def run(self, on_packet, until=None):
        while until is None or time.monotonic() < until:
            timeout = 0.05
            if self.events:
                timeout = max(0.0, min(timeout, self.events[0][0] - time.monotonic()))
            readable, _, _ = select.select([self.sock], [], [], timeout)
            if readable:
                while True:
                    try:
                        data, _ = self.sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    received = now_ns()
                    packet = decode(data)
                    if packet:
                        on_packet(packet, received)
            while self.events and self.events[0][0] <= time.monotonic():
                _, _, fn = heapq.heappop(self.events)
                fn()


# --- virtual players -----------------------------------------------------------

class VirtualPlayer:
    def __init__(self, spec, skew_ms=0.0):
        parts = spec.split(":")
        self.id = parts[0]
        self.bpm = float(parts[1])
        self.phase_ms = float(parts[2]) if len(parts) > 2 else 0.0
        self.mode = MODES[parts[3].lower()] if len(parts) > 3 else 0
        self.skew_ns = int(skew_ms * 1e6)
        self.playing = True
        self.seq = 0
        self.playing_since_ns = now_ns()

    def clock(self):
        return now_ns() + self.skew_ns

    def beats(self, t_ns):
        """Beat position at player time t; beats are phase_ms later than t=0."""
        return (t_ns - self.skew_ns) / 1e9 * self.bpm / 60.0 - self.phase_ms / (60000.0 / self.bpm)

    def state(self):
        t = self.clock()
        if not self.playing:
            # Paused at the position given by phase_ms: beat distance fixed.
            t = self.skew_ns
        b = self.beats(t)
        self.seq += 1
        playing_for = now_ns() - self.playing_since_ns if self.playing else 0
        return encode_state(self.id, self.seq, self.playing, self.mode, self.bpm,
                            b - math.floor(b), math.floor(b), t, playing_for)


def run_players(specs, port, broadcast, skew_ms, jitter_ms, loss, duration=None, quiet=False,
                paused=False, playing_for_s=0.0):
    sock = make_socket(port)
    sched = Scheduler(sock)
    players = [VirtualPlayer(s, skew_ms * i) for i, s in enumerate(specs)]
    for p in players:
        p.playing = not paused
        p.playing_since_ns -= int(playing_for_s * 1e9)
    ids = {p.id: p for p in players}

    def send(data):
        if random.random() < loss:
            return
        delay = random.uniform(0, jitter_ms / 1000.0) if jitter_ms else 0.0
        if delay:
            sched.at(time.monotonic() + delay, lambda: sock.sendto(data, (broadcast, port)))
        else:
            sock.sendto(data, (broadcast, port))

    def state_tick():
        for p in players:
            send(p.state())
        sched.at(time.monotonic() + STATE_INTERVAL, state_tick)

    def on_packet(packet, received_ns):
        ptype, f = packet
        if ptype != T_PING or f["id"] in ids:
            return
        for p in players:
            t2 = received_ns + p.skew_ns
            # Stamped before the jitter delay, so jitter acts like network delay.
            send(encode_pong(p.id, f["id"], f["seq"], f["sent"], t2, p.clock()))

    if not quiet:
        for p in players:
            print(f"{p.id}: {p.bpm} BPM, phase {p.phase_ms:+.1f} ms, mode {p.mode}, "
                  f"clock skew {p.skew_ns / 1e6:+.0f} ms")
    sched.at(time.monotonic(), state_tick)
    sched.run(on_packet, until=None if duration is None else time.monotonic() + duration)


# --- monitor ---------------------------------------------------------------

class Monitor:
    def __init__(self, sock, port, broadcast, monitor_id="monitor"):
        self.sock, self.port, self.broadcast, self.id = sock, port, broadcast, monitor_id
        self.states = {}   # id -> (state dict, received_ns)
        self.clocks = {}   # id -> Clock
        self.ping_seq = 0

    def ping(self):
        self.ping_seq += 1
        self.sock.sendto(encode_ping(self.id, self.ping_seq, now_ns()), (self.broadcast, self.port))

    def on_packet(self, packet, received_ns):
        ptype, f = packet
        if ptype == T_STATE:
            self.states[f["id"]] = (f, received_ns)
        elif ptype == T_PONG and f["to"] == self.id:
            self.clocks.setdefault(f["id"], Clock()).add(f["t1"], f["t2"], f["t3"], received_ns)

    def phase_now(self, pid, t_local):
        """(beats at local time t, bpm) or None."""
        state, _ = self.states[pid]
        est = self.clocks.get(pid, Clock()).estimate()
        if est is None or state["bpm"] <= 0:
            return None
        at_local = state["at_dac"] - est[0]
        beats = state["beat_distance"]
        if state["playing"]:
            beats += (t_local - at_local) / 1e9 * state["bpm"] / 60.0
        return beats, state["bpm"]

    def offsets(self):
        """{(a, b): offset_ms of a against b}."""
        t = now_ns()
        live = sorted(pid for pid, (_, rx) in self.states.items() if (t - rx) / 1e9 < PEER_TIMEOUT)
        result = {}
        for a in live:
            for b in live:
                if a == b:
                    continue
                pa, pb = self.phase_now(a, t), self.phase_now(b, t)
                if pa and pb:
                    result[(a, b)] = wrap_half(pa[0] - pb[0]) * 60000.0 / pa[1]
        return result

    def report(self):
        t = now_ns()
        lines = []
        for pid in sorted(self.states):
            state, rx = self.states[pid]
            if (t - rx) / 1e9 >= PEER_TIMEOUT:
                continue
            est = self.clocks.get(pid, Clock()).estimate()
            clock = f"clock {est[0] / 1e6:+10.3f} ms ±{est[1] / 2e6:.3f}" if est else "clock  -"
            lines.append(f"{pid:>10} {'play' if state['playing'] else 'stop'} "
                         f"{state['playing_for'] / 1e9:7.1f} s "
                         f"{state['bpm']:8.3f} BPM mode {state['mode']}  {clock}")
        for (a, b), ms in sorted(self.offsets().items()):
            lines.append(f"{a:>10} vs {b:<10} {ms:+8.2f} ms")
        return "\n".join(lines) if lines else "no players"


def run_monitor(port, broadcast):
    sock = make_socket(port)
    sched = Scheduler(sock)
    mon = Monitor(sock, port, broadcast)

    def ping():
        mon.ping()
        sched.at(time.monotonic() + PING_INTERVAL, ping)

    def report():
        print("\033[2J\033[H" + time.strftime("%H:%M:%S") + "\n" + mon.report(), flush=True)
        sched.at(time.monotonic() + 1.0, report)

    sched.at(time.monotonic(), ping)
    sched.at(time.monotonic() + 1.0, report)
    sched.run(mon.on_packet)


# --- selftest --------------------------------------------------------------

def selftest(port):
    """Two virtual players 40 ms apart with 3 s clock skew, plus a monitor,
    on one socket per role. Expects the monitor to see -40 / +40 ms."""
    import threading

    port = port + 7  # keep away from running Mixxx players
    t = threading.Thread(target=run_players, daemon=True, args=(
        ["selfA:174", "selfB:174:40"], port, BROADCAST, 3000.0, 0.0, 0.0, 4.0, True))
    t.start()
    sock = make_socket(port)
    sched = Scheduler(sock)
    mon = Monitor(sock, port, BROADCAST)

    def ping():
        mon.ping()
        sched.at(time.monotonic() + PING_INTERVAL, ping)

    sched.at(time.monotonic(), ping)
    sched.run(mon.on_packet, until=time.monotonic() + 3.0)
    offsets = mon.offsets()
    print(mon.report())
    ab = offsets.get(("selfB", "selfA"))
    ok = ab is not None and abs(ab - (-40.0)) < 1.0
    print("selftest", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--broadcast", default=BROADCAST)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("players")
    p.add_argument("--player", action="append", required=True, help="ID:BPM[:PHASE_MS[:MODE]]")
    p.add_argument("--skew-ms", type=float, default=0.0,
                   help="clock skew per player (player i gets i*skew)")
    p.add_argument("--jitter-ms", type=float, default=0.0, help="random send delay 0..N ms")
    p.add_argument("--loss", type=float, default=0.0, help="packet loss 0..1")
    p.add_argument("--playing-for-s", type=float, default=0.0,
                   help="pretend the players started this long ago (automatic master)")
    p.add_argument("--paused", action="store_true",
                   help="players paused at their phase (static offsets, for cross-checks)")
    sub.add_parser("monitor")
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "players":
        run_players(args.player, args.port, args.broadcast, args.skew_ms, args.jitter_ms, args.loss,
                    paused=args.paused, playing_for_s=args.playing_for_s)
    elif args.cmd == "monitor":
        run_monitor(args.port, args.broadcast)
    else:
        sys.exit(selftest(args.port))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
