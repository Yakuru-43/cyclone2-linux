"""Talk to the Cyclone 2's configuration channel through hidraw."""

import time
from pathlib import Path

from . import hidraw
from . import protocol as P

GAMESIR_VID = 0x3537
CONFIG_PIDS = {0x1053: "cable", 0x100B: "dongle"}
CONFIG_USAGE_PAGE = 0xFFF0
# Through the dongle, a command sent right after the previous reply is
# dropped (2026-10-26, captures/31-…, 32-…), so pace like GSC (about one
# command per 20 ms) and keep a gap after each reply. The cable needs neither.
DONGLE_MIN_INTERVAL = 0.02
DONGLE_GAP_AFTER_REPLY = 0.02
READ_RETRIES = 2  # only for commands that change nothing


class DeviceError(Exception):
    pass


def read_range(dev, bank, offset, length):
    """Read any byte range, in protocol-sized chunks."""
    out = bytearray()
    for off in range(offset, offset + length, P.CHUNK):
        out += dev.read(bank, off, min(P.CHUNK, offset + length - off))
    return bytes(out)


def find():
    """[(path, pid)] for real Cyclone 2 config channels (never the proxy's)."""
    return [(d["path"], d["pid"]) for d in hidraw.devices()
            if d["vid"] == GAMESIR_VID and d["pid"] in CONFIG_PIDS
            and CONFIG_USAGE_PAGE in d["usage_pages"] and not d["virtual"]]


class Cyclone2:
    def __init__(self, path, policy=P.check_read_only, log=None, dongle=False):
        self.path = path
        self.policy = policy
        self.log = log
        self.dev = hidraw.Device(path)
        self.last_send = self.last_reply = 0.0
        self.min_interval = DONGLE_MIN_INTERVAL if dongle else 0.0
        self.gap_after_reply = DONGLE_GAP_AFTER_REPLY if dongle else 0.0

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, pkt):
        why = self.policy(pkt)
        if why:
            raise DeviceError(f"refused to send {pkt[:8].hex(' ')}: {why}")
        wait = max(self.last_send + self.min_interval,
                   self.last_reply + self.gap_after_reply) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        if self.log:
            self.log("out", pkt)
        self.last_send = time.monotonic()
        if self.dev.write(pkt) != len(pkt):
            raise DeviceError("short write")

    def transact(self, pkt, expect, timeout=1.0, match=None):
        """Send one command and return the first reply whose opcode is in
        `expect`. Stream reports and busy acks are skipped. Commands that
        change nothing are retried; writes and profile switches are not."""
        idempotent = pkt[1] in (P.OP_READ, P.OP_REFRESH, P.OP_FW_VERSION,
                                P.OP_CURRENT_PROFILE)
        for attempt in range(1 + (READ_RETRIES if idempotent else 0)):
            try:
                return self._transact_once(pkt, expect, timeout, match)
            except DeviceError:
                if attempt == READ_RETRIES or not idempotent:
                    raise

    def _transact_once(self, pkt, expect, timeout, match):
        self.send(pkt)
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            data = bytes(self.dev.read(P.REPORT_LEN, max(1, int(left * 1000))))
            if not data or data[0] != P.REPORT_REPLY:
                continue
            self.last_reply = time.monotonic()
            if self.log:
                self.log("in", data)
            if data[1] == P.OP_ACK and data[2] == 1:  # AckWithBusy
                continue
            if data[1] in expect and (match is None or match(data)):
                return data
        raise DeviceError(f"no reply to {pkt[:6].hex(' ')} within {timeout}s")

    def fw_version(self):
        return P.parse_fw_version(self.transact(P.fw_version(), {P.OP_FW_VERSION_ACK}))

    def current_profile(self):
        return self.transact(P.current_profile(), {P.OP_CURRENT_PROFILE_ACK})[2]

    def read(self, bank, offset, length):
        """Read one chunk. Replies for other chunks (late, stale) are skipped."""
        want = bytes([bank, offset >> 8, offset & 0xFF, length])
        reply = self.transact(P.read_chunk(bank, offset, length), {P.OP_READ_ACK},
                              match=lambda r: r[2:6] == want)
        return P.parse_read_reply(reply)[2]

    def write(self, bank, offset, data):
        """Write, then read the same bytes back and compare.

        A write sets absolute bytes, so resending it is harmless: if the Ack
        is lost (dongle radio drops), check the bytes and resend once only if
        they did not land."""
        data = bytes(data)
        for attempt in (1, 2):
            try:
                self.transact(P.write_chunk(bank, offset, data), {P.OP_ACK})
                break
            except DeviceError as e:
                if self.read(bank, offset, len(data)) == data:
                    break  # it landed; only the Ack was lost
                if attempt == 2:
                    raise DeviceError(f"{e}; the bytes did not land after a resend") from None
        back = self.read(bank, offset, len(data))
        if back != data:
            raise DeviceError(f"read-back mismatch at bank 0x{bank:02x} +0x{offset:03x}: "
                              f"wrote {data.hex(' ')}, read {back.hex(' ')}")
        return back

    def switch_profile(self, n):
        """Switch the active profile; resend once if it did not take."""
        for attempt in (1, 2):
            try:
                self.transact(P.switch_profile(n), {P.OP_ACK})
            except DeviceError:
                if attempt == 2:
                    raise
            if self.current_profile() == n:
                return
        raise DeviceError(f"asked for profile {n}, the controller did not switch")

    def read_bank(self, bank):
        buf = bytearray(P.BANK_SIZE[bank])
        for pkt in P.read_bank(bank):
            off, n = pkt[3] << 8 | pkt[4], pkt[5]
            buf[off:off + n] = self.read(bank, off, n)
        return bytes(buf)
