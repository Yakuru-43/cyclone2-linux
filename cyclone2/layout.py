"""Decode the Cyclone 2's configuration banks (PROTOCOL.md §4)."""

from collections import namedtuple

PROFILE_BANKS = (1, 2, 3, 4, 5)  # 5 is the "Shift" layer
LIGHT_BANK = 0x20
BANK_SIZE = {**{b: 680 for b in PROFILE_BANKS}, LIGHT_BANK: 635}

KEYS = ("Up", "Down", "Left", "Right", "LB", "RB", "LS", "RS",
        "A", "B", "X", "Y", "Home", "View", "Menu", "Share")
REPORT_RATES = {0: "250 Hz", 1: "500 Hz", 2: "1000 Hz"}
MOTOR_LEVELS = {0: 0, 25: 1, 50: 2, 75: 3, 100: 4}

# A writable field: name, bank, offset, length and the allowed value range.
Field = namedtuple("Field", "name bank offset length lo hi")

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
