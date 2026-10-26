#!/usr/bin/env python3
"""List the writes (0F 03 / 0F 07) in a c2proxy capture, with the field each
byte lands on according to PROTOCOL.md §4.

Usage: python3 tools/capture_writes.py CAPTURE.jsonl
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cyclone2.layout import KEYS, LEFT_STICK, LIGHT_BANK, LT, RIGHT_STICK, RT  # noqa: E402

TRIGGER = ["turbo_en", "turbo_speed", "dz_en", "dz_front", "dz_back", "anti_front",
           "anti_back", "map_en", "map1", "map2", "map3", "toggle", "hair_status",
           "hair_start", "hair_end", "curve_en", "curve_type", "curvature"] + \
          [f"curve_p{i // 2 + 1}_{'xy'[i % 2]}" for i in range(10)]
STICK = ["stick_en", "square", "dz_en", "dz_front", "dz_back", "anti_front",
         "anti_back", "curve_en", "curve_type", "curvature"] + \
        [f"curve_p{i // 2 + 1}_{'xy'[i % 2]}" for i in range(10)] + \
        ["map_en", "x_flip", "y_flip", "axis_ratio", "mouse_dpi", "output", "cross",
         "map_up", "map_down", "map_left", "map_right", "map_outline"]
KEY = ["turbo_en", "turbo_speed", "map_en", "map1", "map2", "map3", "toggle"]
FUN = ["motor_left", "motor_right", "motor_lt", "motor_rt", "audio_en", "volume",
       "mixer", "mic_mute", "mic_sens", "shift_en", "shift_value", "dpad_diag_lock",
       "xinput_abxy_swap", "switch_abxy_swap", "report_rate"]


def field(bank, off):
    if bank == LIGHT_BANK:
        if off == 0:
            return "active effect"
        if 1 <= off < 621:
            k, rel = divmod(off - 1, 124)
            if rel < 4:
                return f"anim {k} {['keyframes', 'effects', 'speed', 'brightness'][rel]}"
            f, rem = divmod(rel - 4, 15)
            z, c = divmod(rem, 3)
            return f"anim {k} frame {f} zone {z} {'RGB'[c]}"
        return f"light tail +{off - 621}"
    if off < 0x20:
        return "name"
    if off < 0x40:
        i = off - 0x20
        return FUN[i] if i < len(FUN) else "reserved"
    if off < 0xB0:
        k, i = divmod(off - 0x40, 7)
        return f"key {KEYS[k]} {KEY[i]}"
    if off < 0x1EE:
        side, i = ("L4", off - 0xB0) if off < 0x14F else ("R4", off - 0x14F)
        return f"{side} {KEY[i] if i < 7 else 'macro +' + str(i - 7)}"
    for base, name, names in ((LT, "LT", TRIGGER), (RT, "RT", TRIGGER),
                              (LEFT_STICK, "LS", STICK), (RIGHT_STICK, "RS", STICK)):
        if base <= off < base + len(names):
            return f"{name} {names[off - base]}"
    if off < 0x287:
        return f"motion aim +{off - 0x266}"
    return f"motion tilt +{off - 0x287}"


def main():
    for line in open(sys.argv[1]):
        rec = json.loads(line)
        pkt = bytes.fromhex(rec["hex"])
        if rec["dir"] != "out" or pkt[0] != 0x0F:
            continue
        if pkt[1] == 0x03:
            bank, off, n = pkt[2], pkt[3] << 8 | pkt[4], pkt[5]
            data = pkt[6:6 + n]
            fields = sorted({field(bank, off + i) for i in range(n)},
                            key=lambda s: s)
            print(f"{rec['t']:8.3f} write bank 0x{bank:02x} +0x{off:03x} "
                  f"[{n}] {data.hex(' ')}  -> {', '.join(fields)}"
                  f"{'  ' + rec['note'] if 'note' in rec else ''}")
        elif pkt[1] == 0x07:
            print(f"{rec['t']:8.3f} switch profile {pkt[2]}")


if __name__ == "__main__":
    main()
