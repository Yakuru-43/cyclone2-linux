"""Cyclone 2 vendor-channel packets and send policies (PROTOCOL.md §2).

Every packet our code can send is built here and checked against an
allow-list here. There is no deny-list: anything not allowed is refused.
"""

from .layout import BANK_SIZE

REPORT_OUT = 0x0F     # host -> device commands
REPORT_REPLY = 0x10   # device -> host replies
REPORT_STREAM = 0x12  # device -> host input stream (while heartbeats flow)
REPORT_LEN = 64       # report ID + 63 bytes
CHUNK = 58            # max data bytes per read/write packet

OP_WRITE, OP_READ, OP_READ_ACK, OP_ACK = 0x03, 0x04, 0x05, 0x06
OP_SWITCH_PROFILE, OP_FW_VERSION, OP_FW_VERSION_ACK = 0x07, 0x09, 0x0A
OP_CURRENT_PROFILE, OP_CURRENT_PROFILE_ACK = 0x0B, 0x0C
OP_REFRESH, OP_REFRESH_ACK, OP_HEARTBEAT = 0x10, 0x11, 0xF2

OPCODE_NAMES = {
    0x03: "WriteProfile", 0x04: "ReadProfile", 0x05: "ReadProfileAck",
    0x06: "Ack", 0x07: "SwitchProfile", 0x09: "ReadFWVersion",
    0x0A: "ReadFWVersionAck", 0x0B: "ReadCurrentProfile",
    0x0C: "ReadCurrentProfileAck", 0x0D: "ReadRGB/SetLightStatus",
    0x0E: "ReadRGBAck", 0x0F: "ProfileChanged", 0x10: "RefreshProfile",
    0x11: "RefreshProfileAck", 0x16: "SetMacroStatus", 0x17: "QuickUpdate",
    0x20: "Vibration", 0xF2: "HeartBeat", 0xFC: "EnterUpgradeMode",
    0xFD: "SetCalibrationState",
}

DEVICE_ID_RANGE = range(0x039D, 0x03A4)  # GSC's device info / SetDeviceId


def packet(*payload):
    """A full 64-byte output report: 0x0F, payload, zero padding."""
    if len(payload) > REPORT_LEN - 1:
        raise ValueError("payload too long")
    return bytes([REPORT_OUT, *payload]).ljust(REPORT_LEN, b"\0")


def fw_version():
    return packet(OP_FW_VERSION)


def current_profile():
    return packet(OP_CURRENT_PROFILE)


def heartbeat():
    return packet(OP_HEARTBEAT, 0)


def read_chunk(bank, offset, length):
    return packet(OP_READ, bank, offset >> 8, offset & 0xFF, length)


def read_bank(bank):
    """The ReadProfile packets that cover a whole bank, as GSC sends them."""
    size = BANK_SIZE[bank]
    return [read_chunk(bank, off, min(CHUNK, size - off))
            for off in range(0, size, CHUNK)]


def write_chunk(bank, offset, data):
    if not 1 <= len(data) <= CHUNK:
        raise ValueError("a write carries 1-58 data bytes")
    return packet(OP_WRITE, bank, offset >> 8, offset & 0xFF, len(data), *data)


def check_write(pkt, fields):
    """Allow a WriteProfile only if it lies inside one approved field and every
    value is in that field's range. `fields`: iterable of layout.Field."""
    if len(pkt) != REPORT_LEN or pkt[:2] != bytes([REPORT_OUT, OP_WRITE]):
        return "not a 64-byte write command"
    bank, off, n = pkt[2], pkt[3] << 8 | pkt[4], pkt[5]
    data = pkt[6:6 + n]
    if any(pkt[6 + n:]):
        return "non-zero padding after the data"
    for f in fields:
        if f.bank == bank and f.offset <= off and off + n <= f.offset + f.length:
            if all(f.lo <= v <= f.hi for v in data):
                return None
            return f"value out of range for {f.name} ({f.lo}-{f.hi})"
    return f"bank 0x{bank:02x} +0x{off:03x} [{n}] is not an approved field"


def check_restore(pkt):
    """Policy for `cyclone2 restore`: writes anywhere inside a known bank."""
    if len(pkt) != REPORT_LEN or pkt[:2] != bytes([REPORT_OUT, OP_WRITE]):
        return "not a 64-byte write command"
    bank, off, n = pkt[2], pkt[3] << 8 | pkt[4], pkt[5]
    if bank not in (1, 2, 3, 4, 5, 0x20) or not _in_bank(bank, off, n):
        return f"bank 0x{bank:02x} +0x{off:03x} [{n}] is outside the configuration banks"
    if any(pkt[6 + n:]):
        return "non-zero padding after the data"
    return None


def diff_writes(bank, current, target, gap=4):
    """Write chunks that turn `current` into `target`, touching only differing
    bytes (runs closer than `gap` bytes are merged, chunks are <= 58 bytes)."""
    runs, i = [], 0
    while i < len(target):
        if current[i] == target[i]:
            i += 1
            continue
        j = i
        while j < len(target) and (current[j] != target[j] or any(
                current[k] != target[k] for k in range(j, min(j + gap, len(target))))):
            j += 1
        runs.append((i, j))
        i = j
    chunks = []
    for start, end in runs:
        for off in range(start, end, CHUNK):
            chunks.append((bank, off, bytes(target[off:min(end, off + CHUNK)])))
    return chunks


def _in_bank(bank, offset, length):
    return (bank in BANK_SIZE and 1 <= length <= CHUNK
            and offset + length <= BANK_SIZE[bank])


def check_read_only(pkt):
    """Policy for our CLI before writes are approved (§2.5). None = allowed."""
    if len(pkt) != REPORT_LEN or pkt[0] != REPORT_OUT:
        return "not a 64-byte 0x0F command report"
    op, rest = pkt[1], pkt[2:]
    if op in (OP_FW_VERSION, OP_CURRENT_PROFILE) and not any(rest):
        return None
    if op == OP_HEARTBEAT and not any(rest):
        return None
    if op in (OP_READ, OP_REFRESH):
        bank, off, n = pkt[2], pkt[3] << 8 | pkt[4], pkt[5]
        if _in_bank(bank, off, n) and not any(pkt[6:]):
            return None
        return f"read outside a known bank: bank 0x{bank:02x} +0x{off:03x} [{n}]"
    return f"opcode 0x{op:02x} ({OPCODE_NAMES.get(op, 'unknown')}) is not allowed"


def check_gsc(pkt):
    """Policy for GameSir Connect's traffic through tools/c2proxy.py."""
    if len(pkt) < 2 or pkt[0] != REPORT_OUT:
        return "not a 0x0F command report"
    op = pkt[1]
    if op in (0x04, 0x09, 0x0B, 0x0D, 0x10, 0x16, 0x20, 0xF2):
        return None
    if op == OP_SWITCH_PROFILE:
        return None if len(pkt) > 2 and 1 <= pkt[2] <= 5 else "bad profile number"
    if op == OP_WRITE:
        bank, off, length = pkt[2], pkt[3] << 8 | pkt[4], pkt[5]
        if bank not in (1, 2, 3, 4, 5, 0x20):
            return f"write to bank 0x{bank:02x}"
        if any(a in DEVICE_ID_RANGE for a in range(off, off + length)):
            return "write to the device-ID range"
        return None
    return f"opcode 0x{op:02x} ({OPCODE_NAMES.get(op, 'unknown')}) is not allowed"


def parse_fw_version(reply):
    """10 0A + UTF-16LE: controller version (bytes 2-9), dongle (10-17).

    Returns raw strings such as '0346' and GSC's display form '3.4.6'
    (GSC drops the first character and joins the rest with dots).
    """
    def one(raw):
        text = raw.decode("utf-16-le", "replace").strip("\0 ")
        return {"raw": text, "version": ".".join(text[1:]) if text else None}
    return {"controller": one(reply[2:10]), "dongle": one(reply[10:18])}


def parse_read_reply(reply):
    """10 05 / 10 11: (bank, offset, data)."""
    bank, off, n = reply[2], reply[3] << 8 | reply[4], reply[5]
    return bank, off, bytes(reply[6:6 + n])
