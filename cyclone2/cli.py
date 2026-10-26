"""cyclone2: configure a GameSir Cyclone 2 from Linux.

Read-only for now: every packet goes through protocol.check_read_only.
"""

import argparse
import datetime
import json
import sys
import time

from . import protocol as P
from .device import CONFIG_PIDS, Cyclone2, DeviceError, find
from .layout import LIGHT_BANK, PROFILE_BANKS, decode_light, decode_profile

ALL_BANKS = (*PROFILE_BANKS, LIGHT_BANK)


def bank_name(bank):
    return "light" if bank == LIGHT_BANK else "shift" if bank == 5 else f"profile {bank}"


def parse_bank(text):
    if text == "all":
        return list(ALL_BANKS)
    if text == "light":
        return [LIGHT_BANK]
    if text == "shift":
        return [5]
    n = int(text, 0)
    if n not in ALL_BANKS:
        raise argparse.ArgumentTypeError(f"no such bank: {text}")
    return [n]


def planned_packets(args):
    if args.cmd == "info":
        return [P.fw_version(), P.current_profile()]
    banks = args.bank if args.cmd == "get" else list(ALL_BANKS)
    return [pkt for b in banks for pkt in P.read_bank(b)] + (
        [P.fw_version()] if args.cmd == "backup" else [])


def show_dry_run(args):
    pkts = planned_packets(args)
    print(f"dry run: would send {len(pkts)} packet(s), each 64 bytes "
          f"(0x0F + payload + zero padding):")
    for pkt in pkts:
        why = P.check_read_only(pkt)
        payload = pkt[:max(2, len(pkt.rstrip(b'\0')))]
        print(f"  {payload.hex(' ')}{' + zeros' if len(payload) < 64 else ''}"
              f"  {P.OPCODE_NAMES.get(pkt[1], '?')}"
              f"{'  REFUSED: ' + why if why else ''}")


def open_device(args, log):
    devices = find()
    if args.device:
        devices = [d for d in devices if d[0] == args.device]
    if len(devices) != 1:
        raise DeviceError(f"need exactly one Cyclone 2 in XInput mode (cable or "
                          f"dongle), found {devices or 'none'}")
    path, pid = devices[0]
    return Cyclone2(path, log=log), path, pid


def make_log(path):
    if not path:
        return None
    f = open(path, "a", buffering=1)
    t0 = time.monotonic()

    def log(direction, pkt):
        f.write(json.dumps({"t": round(time.monotonic() - t0, 4), "dir": direction,
                            "hex": pkt.hex(" "),
                            "op": P.OPCODE_NAMES.get(pkt[1], f"0x{pkt[1]:02x}")}) + "\n")
    return log


def cmd_info(dev, path, pid, args):
    fw = dev.fw_version()
    print(f"device:   {path} ({CONFIG_PIDS[pid]}, 3537:{pid:04x})")
    print(f"firmware: controller {fw['controller']['version']} "
          f"(raw {fw['controller']['raw']!r})", end="")
    if fw["dongle"]["raw"]:
        print(f", dongle {fw['dongle']['version']} (raw {fw['dongle']['raw']!r})")
    else:
        print()
    print(f"profile:  {dev.current_profile()}")


def cmd_get(dev, path, pid, args):
    out = {}
    for bank in args.bank:
        data = dev.read_bank(bank)
        out[bank_name(bank)] = decode_light(data) if bank == LIGHT_BANK else decode_profile(data)
    print(json.dumps(out, indent=1 if args.json else None))


def cmd_backup(dev, path, pid, args):
    banks = {f"0x{b:02x}": dev.read_bank(b).hex() for b in ALL_BANKS}
    fw = dev.fw_version()
    doc = {"format": "cyclone2-backup/1",
           "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "device": f"3537:{pid:04x} ({CONFIG_PIDS[pid]})",
           "firmware": {k: v["raw"] for k, v in fw.items()},
           "banks": banks}
    with open(args.file, "x") as f:
        json.dump(doc, f, indent=1)
    print(f"saved {len(banks)} banks to {args.file}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cyclone2", description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--device", help="hidraw path (default: autodetect)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the exact packets, send nothing")
    ap.add_argument("--log", metavar="FILE.jsonl", help="log every packet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="firmware versions and current profile")
    g = sub.add_parser("get", help="read and decode configuration banks")
    g.add_argument("--bank", type=parse_bank, default=[1],
                   help="1-4, shift, light or all (default: 1)")
    g.add_argument("--json", action="store_true", help="indented JSON")
    b = sub.add_parser("backup", help="save every bank to a new JSON file")
    b.add_argument("file")
    args = ap.parse_args(argv)

    if args.dry_run:
        show_dry_run(args)
        return 0
    try:
        dev, path, pid = open_device(args, make_log(args.log))
        with dev:
            {"info": cmd_info, "get": cmd_get, "backup": cmd_backup}[args.cmd](
                dev, path, pid, args)
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
