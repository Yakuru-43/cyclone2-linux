"""Talk to the Cyclone 2's configuration channel through hidraw."""

import time
from pathlib import Path

from . import protocol as P

GAMESIR_VID = 0x3537
CONFIG_PIDS = {0x1053: "cable", 0x100B: "dongle"}
CONFIG_USAGE_PAGE = 0xFFF0


class DeviceError(Exception):
    pass


def _is_virtual(path):
    node = Path(path).name
    return "/virtual/" in str((Path("/sys/class/hidraw") / node / "device").resolve())


def find():
    """[(path, pid)] for real Cyclone 2 config channels (never the proxy's)."""
    import hidraw
    found = []
    for d in hidraw.enumerate(GAMESIR_VID):
        path = d["path"].decode() if isinstance(d["path"], bytes) else d["path"]
        if (d["product_id"] in CONFIG_PIDS and d["usage_page"] == CONFIG_USAGE_PAGE
                and not _is_virtual(path)):
            found.append((path, d["product_id"]))
    return sorted(set(found))


class Cyclone2:
    def __init__(self, path, policy=P.check_read_only, log=None):
        import hidraw
        self.path = path
        self.policy = policy
        self.log = log
        self.dev = hidraw.device()
        self.dev.open_path(path.encode())

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
        if self.log:
            self.log("out", pkt)
        if self.dev.write(pkt) != len(pkt):
            raise DeviceError("short write")

    def transact(self, pkt, expect, timeout=1.0):
        """Send one command and return the first reply whose opcode is in
        `expect`. Stream reports and busy acks are skipped."""
        self.send(pkt)
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            data = bytes(self.dev.read(P.REPORT_LEN, max(1, int(left * 1000))))
            if not data or data[0] != P.REPORT_REPLY:
                continue
            if self.log:
                self.log("in", data)
            if data[1] == P.OP_ACK and data[2] == 1:  # AckWithBusy
                continue
            if data[1] in expect:
                return data
        raise DeviceError(f"no reply to {pkt[:6].hex(' ')} within {timeout}s")

    def fw_version(self):
        return P.parse_fw_version(self.transact(P.fw_version(), {P.OP_FW_VERSION_ACK}))

    def current_profile(self):
        return self.transact(P.current_profile(), {P.OP_CURRENT_PROFILE_ACK})[2]

    def read(self, bank, offset, length):
        reply = self.transact(P.read_chunk(bank, offset, length), {P.OP_READ_ACK})
        rbank, off, data = P.parse_read_reply(reply)
        if (rbank, off, len(data)) != (bank, offset, length):
            raise DeviceError(f"reply {reply[:6].hex(' ')} does not match the request")
        return data

    def write(self, bank, offset, data):
        """Write, then read the same bytes back and compare."""
        self.transact(P.write_chunk(bank, offset, data), {P.OP_ACK})
        back = self.read(bank, offset, len(data))
        if back != bytes(data):
            raise DeviceError(f"read-back mismatch at bank 0x{bank:02x} +0x{offset:03x}: "
                              f"wrote {bytes(data).hex(' ')}, read {back.hex(' ')}")
        return back

    def read_bank(self, bank):
        buf = bytearray(P.BANK_SIZE[bank])
        for pkt in P.read_bank(bank):
            reply = self.transact(pkt, {P.OP_READ_ACK})
            rbank, off, data = P.parse_read_reply(reply)
            want = (pkt[2], pkt[3] << 8 | pkt[4], pkt[5])
            if (rbank, off, len(data)) != want:
                raise DeviceError(f"reply {reply[:6].hex(' ')} does not match "
                                  f"request {pkt[:6].hex(' ')}")
            buf[off:off + len(data)] = data
        return bytes(buf)
