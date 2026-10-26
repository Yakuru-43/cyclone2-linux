#!/usr/bin/env python3
"""Filtering capture proxy between GameSir Connect (under Wine) and a Cyclone 2.

Creates a virtual HID device (uhid) that exposes only the Cyclone 2's vendor
configuration channel (usage page 0xFFF0: output 0x0F, inputs 0x10 / 0x12),
with the real device's VID:PID, and relays traffic to the real hidraw node:

  GameSir Connect --0x0F--> [allow-list] --> real controller
  GameSir Connect <--0x10/0x12-------------- real controller

Commands not on the allow-list (firmware update, upgrade mode, calibration,
device-ID writes, anything unknown) are dropped and reported. Every relayed
or dropped packet is logged to captures/<session>.jsonl.

Usage: python3 -I tools/c2proxy.py SESSION [--stream]
Needs read/write access to /dev/uhid (see README).
To start a new capture file without restarting:
  echo NAME > captures/.rotate
"""

import argparse
import errno
import json
import os
import select
import signal
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAMESIR_VID = 0x3537
CONFIG_PIDS = {0x1053: "cable", 0x100B: "dongle"}
REPORT_LEN = 64  # report ID + 63 bytes, on every vendor report
ROTATE = ROOT / "captures" / ".rotate"

# linux/uhid.h
UHID_DESTROY, UHID_START, UHID_STOP, UHID_OPEN, UHID_CLOSE, UHID_OUTPUT = 1, 2, 3, 4, 5, 6
UHID_GET_REPORT, UHID_GET_REPORT_REPLY, UHID_CREATE2, UHID_INPUT2 = 9, 10, 11, 12
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
UHID_OUTPUT_REPORT = 1
UHID_EVENT_SIZE = 4 + 4372  # type + largest union member (uhid_create2_req)
UHID_DATA_MAX = 4096
BUS_USB = 3

# Channel B exactly as the controller declares it (PROTOCOL.md §1.2), with
# explicit globals so it parses on its own.
RDESC = bytes.fromhex(
    "06f0ff 0940 a101"
    " 0600ff 0900 8510 1500 26ff00 7508 953f 8102"
    " 8512 0921 953f 8102"
    " 850f 0922 953f 9102"
    " c0"
)

sys.path.insert(0, str(ROOT))
from cyclone2.protocol import OPCODE_NAMES as OPCODES, check_gsc as allowed  # noqa: E402


def find_real_device():
    """The real (non-virtual) hidraw node of a Cyclone 2 config channel."""
    found = []
    for node in sorted(Path("/sys/class/hidraw").iterdir()):
        dev = node / "device"
        if "/virtual/" in str(dev.resolve()):
            continue
        ids = dict(l.split("=", 1) for l in (dev / "uevent").read_text().splitlines()
                   if "=" in l)
        _, vid, pid = (int(x, 16) for x in ids.get("HID_ID", "0:0:0").split(":"))
        if vid != GAMESIR_VID or pid not in CONFIG_PIDS:
            continue
        if b"\x06\xf0\xff" not in (dev / "report_descriptor").read_bytes():
            continue
        found.append((node.name, pid, ids.get("HID_NAME", "")))
    return found


class Log:
    def __init__(self, path, stream):
        self.f = open(path, "a", buffering=1)
        self.path = path
        self.stream = stream
        self.t0 = time.monotonic()
        self.skipped_stream = 0

    def rotate(self, path):
        self.f.close()
        self.f = open(path, "a", buffering=1)
        self.path = path
        self.t0 = time.monotonic()

    def write(self, direction, pkt, note=None):
        if pkt[:1] == b"\x12" and not self.stream:
            self.skipped_stream += 1
            return
        rec = {"t": round(time.monotonic() - self.t0, 4), "dir": direction,
               "hex": pkt.hex(" ")}
        if len(pkt) > 1 and pkt[0] in (0x0F, 0x10):
            rec["op"] = OPCODES.get(pkt[1], f"0x{pkt[1]:02x}")
        if note:
            rec["note"] = note
        self.f.write(json.dumps(rec) + "\n")


def uhid_create(fd, name, vid, pid, version):
    req = struct.pack("<128s64s64sHHIIII", name.encode()[:127], b"cyclone2-proxy",
                      b"", len(RDESC), BUS_USB, vid, pid, version, 0)
    req += RDESC.ljust(UHID_DATA_MAX, b"\0")
    os.write(fd, struct.pack("<I", UHID_CREATE2) + req)


def uhid_input(fd, data):
    os.write(fd, struct.pack("<IH", UHID_INPUT2, len(data))
             + data.ljust(UHID_DATA_MAX, b"\0"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("session", help="capture name, e.g. 01-rgb-red-to-blue")
    ap.add_argument("--stream", action="store_true",
                    help="also log the 0x12 input stream (large)")
    args = ap.parse_args()

    devs = find_real_device()
    if len(devs) != 1:
        sys.exit(f"need exactly one Cyclone 2 config channel, found {devs}")
    node, pid, name = devs[0]
    # hidraw -> HID device -> USB interface -> USB device
    usb_dev = (Path("/sys/class/hidraw") / node / "device").resolve().parent.parent
    version = int((usb_dev / "bcdDevice").read_text(), 16)

    (ROOT / "captures").mkdir(exist_ok=True)
    log_path = ROOT / "captures" / f"{args.session}.jsonl"
    log = Log(log_path, args.stream)

    real = os.open(f"/dev/{node}", os.O_RDWR | os.O_NONBLOCK)
    uhid = os.open("/dev/uhid", os.O_RDWR | os.O_NONBLOCK)
    before = {p.name for p in Path("/sys/class/hidraw").iterdir()}
    uhid_create(uhid, f"{name} (cyclone2 proxy)", GAMESIR_VID, pid, version)

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    print(f"real: /dev/{node} ({CONFIG_PIDS[pid]}, 3537:{pid:04x}); "
          f"log: {log_path.relative_to(ROOT)}", flush=True)
    announced = False
    relayed = blocked = 0
    try:
        while not stop:
            if ROTATE.exists():
                name = ROTATE.read_text().strip()
                ROTATE.unlink()
                log.rotate(ROOT / "captures" / f"{name}.jsonl")
                print(f"log: {log.path.relative_to(ROOT)}", flush=True)
            if not announced:
                new = {p.name for p in Path("/sys/class/hidraw").iterdir()} - before
                if new:
                    print(f"virtual: /dev/{new.pop()}", flush=True)
                    announced = True
            ready, _, _ = select.select([real, uhid], [], [], 0.5)
            if real in ready:
                try:
                    pkt = os.read(real, REPORT_LEN)
                except OSError as e:
                    if e.errno == errno.EAGAIN:
                        continue
                    print(f"real device gone ({e}); stopping", flush=True)
                    break
                if pkt[:1] in (b"\x10", b"\x12"):
                    log.write("in", pkt)
                    uhid_input(uhid, pkt)
            if uhid in ready:
                try:
                    ev = os.read(uhid, UHID_EVENT_SIZE)
                except BlockingIOError:
                    continue
                kind = struct.unpack_from("<I", ev)[0]
                pkt, set_report_id = None, None
                if kind == UHID_OUTPUT:
                    size, = struct.unpack_from("<H", ev, 4 + UHID_DATA_MAX)
                    pkt = ev[4:4 + size]
                elif kind == UHID_SET_REPORT:
                    rid, rnum, rtype, size = struct.unpack_from("<IBBH", ev, 4)
                    set_report_id = rid
                    if rtype == UHID_OUTPUT_REPORT:
                        pkt = ev[12:12 + size]
                elif kind == UHID_GET_REPORT:
                    rid, = struct.unpack_from("<I", ev, 4)
                    os.write(uhid, struct.pack("<IIHH", UHID_GET_REPORT_REPLY,
                                               rid, errno.EIO, 0).ljust(UHID_EVENT_SIZE, b"\0"))
                err = 0
                if pkt is not None:
                    why = allowed(pkt)
                    if why:
                        blocked += 1
                        err = errno.EPERM
                        log.write("out", pkt, f"BLOCKED: {why}")
                        print(f"BLOCKED {pkt[:8].hex(' ')}: {why}", flush=True)
                    else:
                        relayed += 1
                        log.write("out", pkt)
                        os.write(real, pkt.ljust(REPORT_LEN, b"\0"))
                if set_report_id is not None:
                    os.write(uhid, struct.pack("<IIH", UHID_SET_REPORT_REPLY,
                                               set_report_id, err).ljust(UHID_EVENT_SIZE, b"\0"))
    finally:
        os.write(uhid, struct.pack("<I", UHID_DESTROY).ljust(UHID_EVENT_SIZE, b"\0"))
        os.close(uhid)
        os.close(real)
        print(f"stopped: {relayed} relayed, {blocked} blocked, "
              f"{log.skipped_stream} stream reports not logged", flush=True)


if __name__ == "__main__":
    main()
