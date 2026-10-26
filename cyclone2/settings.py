"""What cyclone2 may write: the approved field kinds and the send policy.

Shared by the CLI and the GUI. A new field kind is added here only after the
owner has approved it (see CLAUDE.md).
"""

from . import protocol as P
from .keycodes import KEY_CODES
from .layout import (KEYS, PROFILE_BANKS, STICKS, TRIGGERS, hair_trigger, key_map,
                     light_brightness, light_color, motors, stick_deadzone,
                     trigger_deadzone)

# Field kinds the owner has approved for writing, one kind at a time (step 6).
APPROVED_WRITES = [light_brightness(e) for e in range(5)] + [
    light_color(e, f, z) for e in range(5) for f in range(8) for z in range(5)] + [
    stick_deadzone(b, s) for b in PROFILE_BANKS for s in STICKS] + [
    trigger_deadzone(b, t) for b in PROFILE_BANKS for t in TRIGGERS] + [
    hair_trigger(b, t) for b in PROFILE_BANKS for t in TRIGGERS] + [
    key_map(b, k) for b in PROFILE_BANKS for k in KEYS] + [
    motors(b) for b in PROFILE_BANKS]
LIGHT_SETTINGS = ("brightness", "color")
# Remap targets: gamepad buttons and stick directions (keyboard/mouse later).
MAP_TARGETS = {name: code for name, code in KEY_CODES.items() if 0x01 <= code <= 0x1C}


def policy(pkt):
    if pkt[1:2] == bytes([P.OP_WRITE]):
        return P.check_write(pkt, APPROVED_WRITES)
    if pkt[1:2] == bytes([P.OP_SWITCH_PROFILE]):
        return P.check_switch_profile(pkt)
    return P.check_read_only(pkt)
