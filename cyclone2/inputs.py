"""Read what games see: the controller's XInput (xpad) stick and trigger axes.

Opens the event device read-only and without grabbing it, so games keep
working. Standard library only.
"""

import fcntl
import os
import select
import struct
import threading

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
