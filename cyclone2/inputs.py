"""Read what games see: stick and trigger axes from an input event device
(the Cyclone 2's xpad device, or any other gamepad for `cyclone2 sticks`).

Opens the event device read-only and without grabbing it, so games keep
working. Standard library only.
"""

import fcntl
import math
import os
import select
import struct
import sys
import threading
import time
from pathlib import Path

EV_ABS = 0x03
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0, 1, 2, 3, 4, 5
EVENT = struct.Struct("<qqHHi")           # struct input_event on 64-bit Linux
ABSINFO = struct.Struct("<6i")            # value, minimum, maximum, fuzz, flat, resolution


def _eviocgabs(code):
    return (2 << 30) | (ABSINFO.size << 16) | (ord("E") << 8) | (0x40 + code)


class AxisReader:
    """Keeps the latest stick/trigger values in a background thread."""

    def __init__(self, event_path):
        self.path = event_path
        self.fd = os.open(event_path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.info, self.values = {}, {}
        for code in (ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ):
            buf = bytearray(ABSINFO.size)
            try:
                fcntl.ioctl(self.fd, _eviocgabs(code), buf)
            except OSError:
                continue
            value, lo, hi, *_ = ABSINFO.unpack(buf)
            self.info[code], self.values[code] = (lo, hi), value
        self.error = None
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            while not self.stop.is_set():
                if not select.select([self.fd], [], [], 0.5)[0]:
                    continue
                data = os.read(self.fd, EVENT.size * 64)
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, i)
                    if etype == EV_ABS and code in self.info:
                        self.values[code] = value
        except OSError as e:          # unplugged
            self.error = e
        finally:
            os.close(self.fd)

    def close(self):
        self.stop.set()

    def _norm(self, code, signed=True):
        if code not in self.info:
            return None
        lo, hi = self.info[code]
        v = (self.values[code] - lo) / (hi - lo)
        return round(2 * v - 1, 4) if signed else round(v, 4)

    def snapshot(self):
        """Sticks in -1..1 (y up = +1), triggers in 0..1, plus raw values."""
        y = self._norm(ABS_Y)
        ry = self._norm(ABS_RY)
        return {
            "left": [self._norm(ABS_X), -y if y is not None else None],
            "right": [self._norm(ABS_RX), -ry if ry is not None else None],
            "lt": self._norm(ABS_Z, signed=False), "rt": self._norm(ABS_RZ, signed=False),
            "raw": {"left": [self.values.get(ABS_X), self.values.get(ABS_Y)],
                    "right": [self.values.get(ABS_RX), self.values.get(ABS_RY)]},
            "range": {k: self.info.get(c) for k, c in (("x", ABS_X), ("y", ABS_Y))},
        }


def list_gamepads():
    """[(event path, name)] for input devices with stick axes and gamepad buttons."""
    pads = []
    for ev in sorted(Path("/sys/class/input").glob("event*"), key=lambda p: int(p.name[5:])):
        dev = ev / "device"
        try:
            abs_bits = int((dev / "capabilities/abs").read_text().split()[-1], 16)
            key_words = (dev / "capabilities/key").read_text().split()
            name = (dev / "name").read_text().strip()
        except (OSError, ValueError):
            continue
        keys = int("".join(w.rjust(16, "0") for w in key_words), 16)
        if abs_bits & 0b11 == 0b11 and keys >> 0x130 & 1:   # ABS_X+ABS_Y, BTN_SOUTH
            pads.append((f"/dev/input/{ev.name}", name))
    return pads


def measure(event_path, seconds=15, live=print):
    """Record stick positions for `seconds`; return per-stick corner reach."""
    r = AxisReader(event_path)
    corners = {s: [[0.0, 0.0] for _ in range(4)] for s in ("left", "right")}
    maxr = {"left": 0.0, "right": 0.0}
    tty, last_second = sys.stdout.isatty(), None
    end = time.monotonic() + seconds
    try:
        while (left := end - time.monotonic()) > 0:
            snap = r.snapshot()
            for side in ("left", "right"):
                x, y = snap[side]
                if x is None:
                    continue
                maxr[side] = max(maxr[side], math.hypot(x, y))
                deg = (math.degrees(math.atan2(y, x)) + 360) % 360
                for i, d in enumerate((45, 135, 225, 315)):
                    c = corners[side][i]
                    if abs(deg - d) <= 10 and min(abs(x), abs(y)) > min(c):
                        corners[side][i] = [abs(x), abs(y)]
            (lx, ly), (rx, ry) = snap["left"], snap["right"]
            line = (f"{left:4.1f}s  left x {lx or 0:+.2f} y {ly or 0:+.2f}   "
                    f"right x {rx or 0:+.2f} y {ry or 0:+.2f}")
            if tty:
                live("\r" + line, end="", flush=True)
            elif int(left) != last_second:          # not a terminal: once a second
                last_second = int(left)
                live(line, flush=True)
            time.sleep(0.01)
    finally:
        r.close()
        if tty:
            live("")
    return {s: {"corners": corners[s], "max_distance": maxr[s]} for s in corners}


def verdict(corners):
    seen = [c for c in corners if c[0] > 0.3]
    if not seen:
        return "corners not reached"
    avg = sum(min(c) for c in seen) / len(seen)
    return ("square-like: both axes reach full on diagonals" if avg >= 0.9 else
            "between circle and square" if avg >= 0.78 else
            "circular: on diagonals each axis stops near 71%")
