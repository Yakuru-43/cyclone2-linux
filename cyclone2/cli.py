"""cyclone2: configure a GameSir Cyclone 2 from Linux.

Every packet goes through protocol.check_read_only, or for writes through
protocol.check_write with the fields in APPROVED_WRITES.
"""

import argparse
import datetime
import json
import sys
import time

from . import protocol as P
from .device import CONFIG_PIDS, Cyclone2, DeviceError, find
from .keycodes import KEY_CODES, KEY_NAMES
from .layout import (HAIR_TRIGGER_MODES, KEYS, LIGHT_BANK, LIGHT_ZONES, PROFILE_BANKS,
                     STICKS, TRIGGERS, decode_light, decode_profile, hair_trigger, key_map,
                     light_brightness, light_color, stick_deadzone, trigger_deadzone)

ALL_BANKS = (*PROFILE_BANKS, LIGHT_BANK)

# Field kinds the owner has approved for writing, one kind at a time (step 6).
APPROVED_WRITES = [light_brightness(e) for e in range(5)] + [
    light_color(e, f, z) for e in range(5) for f in range(8) for z in range(5)] + [
    stick_deadzone(b, s) for b in PROFILE_BANKS for s in STICKS] + [
    trigger_deadzone(b, t) for b in PROFILE_BANKS for t in TRIGGERS] + [
    hair_trigger(b, t) for b in PROFILE_BANKS for t in TRIGGERS] + [
    key_map(b, k) for b in PROFILE_BANKS for k in KEYS]
LIGHT_SETTINGS = ("brightness", "color")
# Remap targets: gamepad buttons and stick directions (keyboard/mouse later).
MAP_TARGETS = {name: code for name, code in KEY_CODES.items() if 0x01 <= code <= 0x1C}


def policy(pkt):
    if pkt[1:2] == bytes([P.OP_WRITE]):
        return P.check_write(pkt, APPROVED_WRITES)
    if pkt[1:2] == bytes([P.OP_SWITCH_PROFILE]):
        return P.check_switch_profile(pkt)
    return P.check_read_only(pkt)


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
    if args.cmd == "set" and args.setting in LIGHT_SETTINGS:
        f, value = set_target(args, args.effect)
        return [P.read_chunk(LIGHT_BANK, 0, 1),          # active effect
                P.read_chunk(f.bank, f.offset, f.length),  # old value, for the message
                P.write_chunk(f.bank, f.offset, value),
                P.read_chunk(f.bank, f.offset, f.length)]  # read-back
    if args.cmd == "set":
        f, (pre_off, pre_len), data = profile_plan(args, args.profile)
        return [P.current_profile(),
                P.read_chunk(f.bank, pre_off, pre_len),  # current value (and enable byte)
                P.write_chunk(f.bank, f.offset, data),
                P.read_chunk(f.bank, f.offset, len(data))]
    if args.cmd == "profile":
        return [P.current_profile(), P.switch_profile(args.number), P.current_profile()]
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
        why = policy(pkt)
        if pkt[1] == P.OP_WRITE:
            used = 6 + pkt[5]
        elif pkt[1] in (P.OP_READ, P.OP_REFRESH):
            used = 6
        else:
            used = max(2, len(pkt.rstrip(b"\0")))
        payload = pkt[:used]
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
    return Cyclone2(path, policy=policy, log=log), path, pid


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


def set_target(args, effect):
    """(Field, bytes) for a `set` command on the given effect."""
    if args.setting == "brightness":
        return light_brightness(effect), bytes([args.value])
    zone, rgb = args.value
    return light_color(effect, args.frame, zone), rgb


def show(field, data):
    return "#" + data.hex() if field.length == 3 else str(data[0])


def profile_plan(args, bank):
    """(field, (pre-read offset, length), data) for a per-profile setting."""
    if args.setting == "deadzone":
        target, inner, outer = args.value
        f = (stick_deadzone if target in STICKS else trigger_deadzone)(bank, target)
        data = bytes([inner] if outer is None else [inner, outer])
        return f, (f.offset - 1, 3), data  # enable, inner, outer
    if args.setting == "map":
        key, target = args.value
        f = key_map(bank, key)
        data = b"\0\0" if target == key else bytes([1, MAP_TARGETS[target]])
        return f, (f.offset, 2), data
    target, mode = args.value
    f = hair_trigger(bank, target)
    return f, (f.offset, 1), bytes([HAIR_TRIGGER_MODES[mode]])


def cmd_set_profile_setting(dev, args):
    current = dev.current_profile()
    bank = current if args.profile is None else args.profile
    f, (pre_off, pre_len), data = profile_plan(args, bank)
    old = dev.read(f.bank, pre_off, pre_len)
    if args.setting == "deadzone":
        enabled, old_inner, old_outer = old
        if not enabled:
            raise DeviceError(f"{f.name} is disabled in this profile; not writing")
        outer = old_outer if len(data) == 1 else data[1]
        if not data[0] < outer:
            raise DeviceError(f"the inner deadzone must be below the outer one ({outer})")
        change = f"{old_inner}-{old_outer} -> {data[0]}-{outer}"
    elif args.setting == "map":
        key = args.value[0]
        def output(en, code):
            return KEY_NAMES.get(code, f"0x{code:02x}") if en else key
        change = f"{key} outputs {output(*old)} -> {output(*data)}"
    else:
        names = {v: k for k, v in HAIR_TRIGGER_MODES.items()}
        change = f"{names.get(old[0], f'raw 0x{old[0]:02x}')} -> {names[data[0]]}"
    dev.write(f.bank, f.offset, data)
    print(f"{f.name}: {change} (written and read back)"
          f"{'' if bank == current else ' (not the active profile)'}")


def cmd_profile(dev, path, pid, args):
    old = dev.current_profile()
    dev.transact(P.switch_profile(args.number), {P.OP_ACK})
    now = dev.current_profile()
    if now != args.number:
        raise DeviceError(f"asked for profile {args.number}, controller reports {now}")
    print(f"active profile: {old} -> {now}")


def cmd_set(dev, path, pid, args):
    if args.setting not in LIGHT_SETTINGS:
        return cmd_set_profile_setting(dev, args)
    active = dev.read(LIGHT_BANK, 0, 1)[0]
    effect = active if args.effect is None else args.effect
    f, value = set_target(args, effect)
    old = dev.read(f.bank, f.offset, f.length)
    dev.write(f.bank, f.offset, value)
    print(f"{f.name}: {show(f, old)} -> {show(f, value)} (written and read back)"
          f"{'' if effect == active else ' (not the active effect)'}")


def deadzone_value(text):
    """'left=15' or 'lt=5:95' -> ('left', 15, 95 or None)."""
    stick, _, rng = text.partition("=")
    if stick not in STICKS and stick not in TRIGGERS:
        raise argparse.ArgumentTypeError("target must be left, right, lt or rt")
    inner, _, outer = rng.partition(":")
    inner = percent(inner)
    outer = percent(outer) if outer else None
    if outer is not None and not inner < outer:
        raise argparse.ArgumentTypeError("inner must be below outer")
    return stick, inner, outer


def hair_trigger_value(text):
    """'lt=adaptive' -> ('lt', 'adaptive')."""
    trigger, _, mode = text.partition("=")
    if trigger not in TRIGGERS or mode not in HAIR_TRIGGER_MODES:
        raise argparse.ArgumentTypeError(
            f"use lt|rt={'|'.join(HAIR_TRIGGER_MODES)} (fixed mode is not confirmed yet)")
    return trigger, mode


def map_value(text):
    """'A=B' -> ('A', 'B'). 'A=A' resets A to itself."""
    key, _, target = text.partition("=")
    if key not in KEYS:
        raise argparse.ArgumentTypeError(f"button must be one of {', '.join(KEYS)}")
    if target != key and target not in MAP_TARGETS:
        raise argparse.ArgumentTypeError(f"target must be one of {', '.join(MAP_TARGETS)}")
    return key, target


def profile_bank(text):
    if text == "shift":
        return 5
    n = int(text)
    if n not in (1, 2, 3, 4):
        raise argparse.ArgumentTypeError("profile must be 1-4 or shift")
    return n


def zone_color(text):
    """'right=#ff0500' -> (zone index, b'\xff\x05\x00')."""
    name, _, color = text.partition("=")
    if name not in LIGHT_ZONES:
        raise argparse.ArgumentTypeError(f"zone must be one of {', '.join(LIGHT_ZONES)}")
    color = color.lstrip("#")
    if len(color) != 6:
        raise argparse.ArgumentTypeError("colour must be #RRGGBB")
    return LIGHT_ZONES[name], bytes.fromhex(color)


def load_backup(path):
    doc = json.load(open(path))
    banks = doc["banks"] if "banks" in doc else doc  # older backups: {bank: hex}
    return {int(k, 16): bytes.fromhex(v) for k, v in banks.items()}


def cmd_restore(dev, path, pid, args):
    target = load_backup(args.file)
    plan = []
    for bank in sorted(target):
        if len(target[bank]) != P.BANK_SIZE.get(bank):
            raise DeviceError(f"backup bank 0x{bank:02x} has the wrong size")
        plan += P.diff_writes(bank, dev.read_bank(bank), target[bank])
    if not plan:
        print("the controller already matches the backup")
        return
    print(f"{len(plan)} write(s), {sum(len(d) for _, _, d in plan)} bytes:")
    for bank, off, data in plan:
        print(f"  {P.write_chunk(bank, off, data)[:6 + len(data)].hex(' ')} + zeros")
    if args.plan:
        print("(plan only: nothing written)")
        return
    dev.policy = lambda pkt: (P.check_restore(pkt) if pkt[1] == P.OP_WRITE
                              else P.check_read_only(pkt))
    for bank, off, data in plan:
        dev.write(bank, off, data)
    print("restored and read back")


def percent(text):
    v = int(text)
    if not 0 <= v <= 100:
        raise argparse.ArgumentTypeError("must be 0-100")
    return v


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
    st = sub.add_parser("set", help="change one setting (writes to the controller)")
    st.add_argument("setting", choices=["brightness", "color", "deadzone", "hair-trigger",
                                        "map"])
    st.add_argument("value", help="brightness: 0-100\ncolor: ZONE=#RRGGBB, ZONE in "
                    + ", ".join(LIGHT_ZONES) + "\ndeadzone: left|right|lt|rt=INNER[:OUTER]"
                    "\nhair-trigger: lt|rt=" + "|".join(HAIR_TRIGGER_MODES)
                    + "\nmap: BUTTON=TARGET (BUTTON=BUTTON resets it)")
    st.add_argument("--profile", type=profile_bank, metavar="1-4|shift",
                    help="profile for deadzone / hair-trigger / map (default: the active one)")
    st.add_argument("--frame", type=lambda t: int(t) - 1, default=0, metavar="1-8",
                    help="animation frame for color (default: 1)")
    st.add_argument("--effect", type=lambda t: int(t) - 1, metavar="1-5",
                    help="lighting effect (default: the active one)")
    pr = sub.add_parser("profile", help="switch the active profile (1-4)")
    pr.add_argument("number", type=int, choices=[1, 2, 3, 4])
    r = sub.add_parser("restore", help="write back a backup (only differing bytes)")
    r.add_argument("file")
    r.add_argument("--plan", action="store_true",
                   help="read the controller and print the writes, write nothing")
    args = ap.parse_args(argv)
    if args.cmd == "set":
        try:
            args.value = {"brightness": percent, "color": zone_color,
                          "deadzone": deadzone_value,
                          "hair-trigger": hair_trigger_value,
                          "map": map_value}[args.setting](args.value)
        except (argparse.ArgumentTypeError, ValueError) as e:
            ap.error(f"set {args.setting}: {e}")

    if args.dry_run and args.cmd == "restore":
        ap.error("restore needs the controller's current state: use --plan instead")
    if args.dry_run and args.cmd == "set":
        if args.setting in LIGHT_SETTINGS and args.effect is None:
            ap.error("--dry-run needs --effect (the active one is read from the device)")
        if args.setting not in LIGHT_SETTINGS and args.profile is None:
            ap.error("--dry-run needs --profile (the active one is read from the device)")
    if args.dry_run:
        show_dry_run(args)
        return 0
    try:
        dev, path, pid = open_device(args, make_log(args.log))
        with dev:
            {"info": cmd_info, "get": cmd_get, "backup": cmd_backup,
             "set": cmd_set, "profile": cmd_profile, "restore": cmd_restore}[args.cmd](
                dev, path, pid, args)
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
