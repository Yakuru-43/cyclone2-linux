#!/usr/bin/env python3
"""Rebuild configuration banks from a c2proxy capture and summarise them.

Usage: python3 tools/banks_from_capture.py CAPTURE.jsonl [--save BACKUP.json]

Uses the ReadProfileAck / RefreshProfileAck replies (10 05 / 10 11) in the
capture. With --save, writes {bank: hex} for every bank that was read in full.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cyclone2.layout import BANK_SIZE, LIGHT_BANK, decode_light, decode_profile  # noqa: E402


def banks_from(path):
    banks, seen = {}, {}
    for line in open(path):
        rec = json.loads(line)
        pkt = bytes.fromhex(rec["hex"])
        if rec["dir"] != "in" or pkt[0] != 0x10 or pkt[1] not in (0x05, 0x11):
            continue
        bank, off, n = pkt[2], pkt[3] << 8 | pkt[4], pkt[5]
        if bank not in BANK_SIZE:
            continue
        buf = banks.setdefault(bank, bytearray(BANK_SIZE[bank]))
        buf[off:off + n] = pkt[6:6 + n]
        seen.setdefault(bank, set()).update(range(off, off + n))
    return {b: bytes(v) for b, v in banks.items()
            if len(seen[b]) == BANK_SIZE[b]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("capture")
    ap.add_argument("--save", metavar="BACKUP.json")
    args = ap.parse_args()

    banks = banks_from(args.capture)
    for bank, data in sorted(banks.items()):
        if bank == LIGHT_BANK:
            print(f"bank 0x{bank:02x} (light):", json.dumps(decode_light(data)))
        else:
            p = decode_profile(data)
            vib = {k: v["level"] for k, v in p["vibration"].items()}
            dz = lambda s: f"{p[s]['deadzone']['front']}-{p[s]['deadzone']['back']}"
            print(f"bank {bank} ({'shift' if bank == 5 else 'profile'}): "
                  f"name={p['name']!r} vibration={vib} rate={p['report_rate']} "
                  f"LS={dz('left_stick')} RS={dz('right_stick')} "
                  f"LT={dz('lt')} RT={dz('rt')}")
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        with open(args.save, "w") as f:
            json.dump({f"0x{b:02x}": d.hex() for b, d in sorted(banks.items())},
                      f, indent=1)
        print(f"saved {len(banks)} banks to {args.save}")


if __name__ == "__main__":
    main()
