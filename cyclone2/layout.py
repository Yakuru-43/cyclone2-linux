"""Decode the Cyclone 2's configuration banks (PROTOCOL.md §4)."""

from collections import namedtuple

PROFILE_BANKS = (1, 2, 3, 4, 5)  # 5 is the "Shift" layer
LIGHT_BANK = 0x20
BANK_SIZE = {**{b: 680 for b in PROFILE_BANKS}, LIGHT_BANK: 635}

KEYS = ("Up", "Down", "Left", "Right", "LB", "RB", "LS", "RS",
        "A", "B", "X", "Y", "Home", "View", "Menu", "Share")
REPORT_RATES = {0: "250 Hz", 1: "500 Hz", 2: "1000 Hz"}
MOTOR_LEVELS = {0: 0, 25: 1, 50: 2, 75: 3, 100: 4}

# A writable field: name, bank, offset, length, the allowed value range, and
# optionally an explicit set of allowed values.
Field = namedtuple("Field", "name bank offset length lo hi values", defaults=(None,))

LT, RT = 0x1EE, 0x20A
LEFT_STICK, RIGHT_STICK = 0x226, 0x246


def _deadzone(b, off):
    return {"enabled": bool(b[off]), "front": b[off + 1], "back": b[off + 2],
            "anti_front": b[off + 3], "anti_back": b[off + 4]}


def _trigger(b, base):
    status = b[base + 12]
    return {
        "deadzone": _deadzone(b, base + 2),
        "hair_trigger": {"enabled": bool(status & 0x80), "mode": status & 0x03,
                         "start": b[base + 13], "end": b[base + 14]},
        "curve": {"enabled": bool(b[base + 15]), "type": b[base + 16],
                  "curvature": b[base + 17]},
    }


def _stick(b, base):
    return {"square": bool(b[base + 1]), "deadzone": _deadzone(b, base + 2),
            "curve": {"enabled": bool(b[base + 7]), "type": b[base + 8],
                      "curvature": b[base + 9]},
            "output": b[base + 25]}


def decode_profile(b):
    if len(b) != 680:
        raise ValueError(f"profile bank must be 680 bytes, got {len(b)}")
    motors = [b[0x020 + i] for i in range(4)]
    return {
        "name": bytes(b[0:32]).split(b"\0", 1)[0].decode("utf-8", "replace"),
        "vibration": {k: {"raw": v, "level": MOTOR_LEVELS.get(v)}
                      for k, v in zip(("left", "right", "lt", "rt"), motors)},
        "dpad_diagonal_lock": bool(b[0x02B] & 1),
        "xinput_abxy_swap": bool(b[0x02C] & 1),
        "switch_abxy_swap": bool(b[0x02D] & 1),
        "report_rate": REPORT_RATES.get(b[0x02E], f"raw {b[0x02E]}"),
        "keys": {k: {"turbo": b[0x040 + 7 * i + 1] if b[0x040 + 7 * i] else 0,
                     "mapped": bool(b[0x040 + 7 * i + 2]),
                     "map": list(b[0x040 + 7 * i + 3:0x040 + 7 * i + 6])}
                 for i, k in enumerate(KEYS)},
        "lt": _trigger(b, LT),
        "rt": _trigger(b, RT),
        "left_stick": _stick(b, LEFT_STICK),
        "right_stick": _stick(b, RIGHT_STICK),
    }


def decode_light(b):
    if len(b) != 635:
        raise ValueError(f"light bank must be 635 bytes, got {len(b)}")
    animations = []
    for k in range(5):
        a = 1 + 124 * k
        frames = [[f"#{b[a + 4 + 15 * f + 3 * z]:02x}{b[a + 5 + 15 * f + 3 * z]:02x}"
                   f"{b[a + 6 + 15 * f + 3 * z]:02x}" for z in range(5)]
                  for f in range(b[a] if 1 <= b[a] <= 8 else 0)]
        animations.append({"keyframes": b[a], "effects": b[a + 1],
                           "speed": 20 - b[a + 2], "brightness": b[a + 3],
                           "frames": frames})
    return {
        "active": b[0] if b[0] <= 3 else 0,
        "animations": animations,
        "profile_led": f"#{b[0x26F]:02x}{b[0x270]:02x}{b[0x271]:02x}",
        "standby_time": b[0x273],
    }


def light_brightness(effect):
    """Brightness of one lighting effect (animation 0-4), 0-100."""
    if not 0 <= effect <= 4:
        raise ValueError("effect must be 0-4")
    return Field(f"light.effect{effect}.brightness", LIGHT_BANK,
                 1 + 124 * effect + 3, 1, 0, 100)


# Data zones in each lighting frame (PROTOCOL.md §4.9). "home" is InFract's
# label only (unconfirmed); zone 2 shows nothing on the owner's controller.
LIGHT_ZONES = {"left": 0, "right": 1, "zone2": 2, "home": 3, "m": 4}


def light_color(effect, frame, zone):
    """One zone's colour (R, G, B) in one frame of one lighting effect."""
    if not (0 <= effect <= 4 and 0 <= frame <= 7 and 0 <= zone <= 4):
        raise ValueError("effect 0-4, frame 0-7, zone 0-4")
    return Field(f"light.effect{effect}.frame{frame}.zone{zone}", LIGHT_BANK,
                 1 + 124 * effect + 4 + 15 * frame + 3 * zone, 3, 0, 255)


STICKS = {"left": LEFT_STICK, "right": RIGHT_STICK}


def stick_deadzone(bank, stick):
    """Inner and outer deadzone of one stick in one profile bank, in percent.

    Curve points are stored relative to this range (0-255 between inner and
    outer), so changing the deadzone keeps the curve's shape."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.{stick}_stick.deadzone", bank, STICKS[stick] + 3, 2, 0, 100)


TRIGGERS = {"lt": LT, "rt": RT}
# Hair-trigger status values confirmed on the device (capture 05). Fixed mode
# (presumably 0x82) stays out until it is confirmed.
HAIR_TRIGGER_MODES = {"off": 0x00, "adaptive": 0x81}


def trigger_deadzone(bank, trigger):
    """Inner and outer deadzone of LT or RT in one profile bank, in percent."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.{trigger}.deadzone", bank, TRIGGERS[trigger] + 3, 2, 0, 100)


def hair_trigger(bank, trigger):
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.{trigger}.hair_trigger", bank, TRIGGERS[trigger] + 12, 1,
                 0, 0xFF, frozenset(HAIR_TRIGGER_MODES.values()))


def key_map(bank, key):
    """A key block's (map_en, map_value1): what the button outputs (0x040 + 7i + 2)."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.key.{key}.map", bank, 0x040 + 7 * KEYS.index(key) + 2,
                 2, 0, 0x1C)


MOTOR_RAW = {0: 0, 1: 25, 2: 50, 3: 75, 4: 100}  # GSC levels -> stored bytes
MOTORS = {"left": 0x020, "right": 0x021}


def motors(bank):
    """Left and right rumble motor strength in one profile bank (0x020-0x021)."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.vibration", bank, 0x020, 2, 0, 100,
                 frozenset(MOTOR_RAW.values()))


# Back buttons: 159-byte blocks (PROTOCOL.md §4.5): the 7-byte key header,
# then macro flags (+7), cycle time (+8-9), step count (+10), 30 steps (+11).
BACK_KEYS = {"L4": 0x0B0, "R4": 0x14F}
BACK_BLOCK = 159
# Factory "maps to itself" for back buttons: map_en 1 + the button's own code.
BACK_SELF = {"L4": 0x11, "R4": 0x12}


def back_map(bank, key):
    """A back button's (map_en, map_value1)."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.{key}.map", bank, BACK_KEYS[key] + 2, 2, 0, 0x1C)


def back_macro_flags(bank, key):
    """Macro flags with the 'enabled' bit clear: writes can only turn a macro off."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.{key}.macro_flags", bank, BACK_KEYS[key] + 7, 1, 0, 0xFF,
                 frozenset({0x00, 0x02, 0x04, 0x06}))


def back_macro_steps(bank, key):
    """Step count and steps: writes can only zero them (clear the macro)."""
    if bank not in PROFILE_BANKS:
        raise ValueError("bank must be 1-5")
    return Field(f"profile{bank}.{key}.macro_steps", bank, BACK_KEYS[key] + 10,
                 BACK_BLOCK - 10, 0, 0, frozenset({0}))


def decode_back(block, key, names):
    """What a back button does: (target name, its own name, or None if
    custom) and its macro steps (None if no macro)."""
    en, code = block[2], block[3]
    target = key if (not en or code == BACK_SELF[key]) else names.get(code)
    macro = None
    if block[7] & 1 and block[10]:
        macro = [{"key": names.get(block[11 + 5 * i], f"0x{block[11 + 5 * i]:02x}"),
                  "hold_ms": block[12 + 5 * i] << 8 | block[13 + 5 * i]}
                 for i in range(min(block[10], 30))]
    return target, macro


def back_map_writes(bank, key, block, target_code):
    """[(label, offset, old, new)] to map a back button the way GameSir
    Connect does: picking a button clears the button's macro (flag off, step
    count and steps zeroed), then the mapping is set. Only bytes that change
    are written."""
    base, writes = BACK_KEYS[key], []
    if block[7] & 1:
        writes.append((f"{key}: turn off its macro", base + 7, block[7:8],
                       bytes([block[7] & ~1 & 0xFF])))
    tail = block[10:BACK_BLOCK]
    if any(tail):
        last = max(i for i, v in enumerate(tail) if v)
        for off in range(0, last + 1, 58):
            n = min(58, last + 1 - off)
            writes.append((f"{key}: clear macro steps", base + 10 + off, tail[off:off + n],
                           bytes(n)))
    new = bytes([1, target_code])
    if block[2:4] != new:
        writes.append((f"{key} → mapping", base + 2, block[2:4], new))
    return writes
