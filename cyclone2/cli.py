"""cyclone2: configure a GameSir Cyclone 2 from Linux (XInput mode, cable or dongle).

Every packet is checked against an allow-list before it is sent: reads, and
writes to the specific settings `set` and `profile` support. Firmware update,
calibration and anything unknown are always refused.
"""

import argparse
import datetime
import json
import sys
import time

from . import protocol as P
from .device import CONFIG_PIDS, Cyclone2, DeviceError, find, read_range
from .keycodes import KEY_CODES, KEY_NAMES
from .layout import (BACK_BLOCK, BACK_KEYS, BACK_SELF, HAIR_TRIGGER_MODES, KEYS,
                     LIGHT_BANK, LIGHT_ZONES, MOTORS, MOTOR_RAW, PROFILE_BANKS, STICKS,
                     TRIGGERS, back_map_writes, decode_back, decode_light, decode_profile,
                     hair_trigger, key_map, light_brightness, light_color, motors,
                     stick_deadzone, trigger_deadzone)
from .rumble import find_event_node, rumble
from .settings import APPROVED_WRITES, LIGHT_SETTINGS, MAP_TARGETS, policy  # noqa: F401

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
    return Cyclone2(path, policy=policy, log=log, dongle=CONFIG_PIDS[pid] == "dongle"), path, pid


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
    if args.setting == "vibration":
        side, level = args.value
        f = motors(bank)
        if side is None:
            return f, (f.offset, 2), bytes([MOTOR_RAW[level]] * 2)
        return (f._replace(offset=MOTORS[side], length=1), (f.offset, 2),
                bytes([MOTOR_RAW[level]]))
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
    elif args.setting == "vibration":
        levels = {v: k for k, v in MOTOR_RAW.items()}
        lv = lambda raw: str(levels.get(raw, f"raw {raw}"))
        new = bytearray(old)
        new[f.offset - 0x020:f.offset - 0x020 + len(data)] = data
        change = (f"left {lv(old[0])}, right {lv(old[1])} -> "
                  f"left {lv(new[0])}, right {lv(new[1])} (levels 0-4)")
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
    dev.switch_profile(args.number)
    print(f"active profile: {old} -> {args.number}")


def cmd_rumble(path, args):
    node = find_event_node(path)
    print(f"rumble on {node}: strong {args.strong}%, weak {args.weak}%, {args.ms} ms")
    rumble(node, args.strong, args.weak, args.ms)


def cmd_set_back(dev, args):
    """set map L4=… / R4=…: clear the button's macro if it has one (as GameSir
    Connect does when you pick a button), then set the mapping."""
    key, target = args.value
    current = dev.current_profile()
    bank = current if args.profile is None else args.profile
    base = BACK_KEYS[key]
    block = read_range(dev, bank, base, BACK_BLOCK)
    was, macro = decode_back(block, key, KEY_NAMES)
    code = BACK_SELF[key] if target == key else MAP_TARGETS[target]
    writes = back_map_writes(bank, key, block, code)
    before = (f"macro ({', '.join(s['key'] for s in macro)})" if macro
              else was or "custom")
    where = f"profile {bank}{'' if bank == current else ' (not the active profile)'}"
    if not writes:
        print(f"{key} on {where} already outputs {target}")
        return
    print(f"{key} on {where}: {before} -> {target}. Writes:")
    for label, off, old, new in writes:
        print(f"  {P.write_chunk(bank, off, new)[:6 + len(new)].hex(' ')} + zeros   "
              f"{label.replace('mapping', target)}")
    if args.plan:
        print("(plan only: nothing written)")
        return
    for label, off, old, new in writes:
        dev.write(bank, off, new)
    print("written and read back")


def cmd_set(dev, path, pid, args):
    if args.setting == "map" and args.value[0] in BACK_KEYS:
        return cmd_set_back(dev, args)
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


def vibration_value(text):
    """'3' (both motors) or 'left=2' / 'right=4' -> (side or None, level)."""
    side, _, level = text.rpartition("=")
    if side not in ("", *MOTORS):
        raise argparse.ArgumentTypeError("use LEVEL, left=LEVEL or right=LEVEL")
    if level not in ("0", "1", "2", "3", "4"):
        raise argparse.ArgumentTypeError("level must be 0-4")
    return side or None, int(level)


def map_value(text):
    """'A=B' -> ('A', 'B'). 'A=A' resets A to itself. L4/R4 too."""
    key, _, target = text.partition("=")
    if key not in KEYS and key not in BACK_KEYS:
        raise argparse.ArgumentTypeError(
            f"button must be one of {', '.join((*KEYS, *BACK_KEYS))}")
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
    """Entry point for the `cyclone2` command."""
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
                                        "map", "vibration"])
    st.add_argument("value", help="brightness: 0-100\ncolor: ZONE=#RRGGBB, ZONE in "
                    + ", ".join(LIGHT_ZONES) + "\ndeadzone: left|right|lt|rt=INNER[:OUTER]"
                    "\nhair-trigger: lt|rt=" + "|".join(HAIR_TRIGGER_MODES)
                    + "\nmap: BUTTON=TARGET (BUTTON=BUTTON resets it)"
                    + "\nvibration: LEVEL | left=LEVEL | right=LEVEL (0-4)")
    st.add_argument("--plan", action="store_true",
                    help="map L4/R4: read the controller, print the writes, write nothing")
    st.add_argument("--profile", type=profile_bank, metavar="1-4|shift",
                    help="profile for deadzone / hair-trigger / map (default: the active one)")
    st.add_argument("--frame", type=lambda t: int(t) - 1, default=0, metavar="1-8",
                    help="animation frame for color (default: 1)")
    st.add_argument("--effect", type=lambda t: int(t) - 1, metavar="1-5",
                    help="lighting effect (default: the active one)")
    pr = sub.add_parser("profile", help="switch the active profile (1-4)")
    pr.add_argument("number", type=int, choices=[1, 2, 3, 4])
    ru = sub.add_parser("rumble", help="test rumble like a game would (xpad force feedback)")
    ru.add_argument("--strong", type=percent, default=100, help="left/large motor, %%")
    ru.add_argument("--weak", type=percent, default=100, help="right/small motor, %%")
    ru.add_argument("--ms", type=int, default=1000, help="duration (default 1000)")
    gu = sub.add_parser("gui", help="open the graphical interface in your browser")
    gu.add_argument("--no-browser", action="store_true",
                    help="only print the address; open it yourself")
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
                          "map": map_value,
                          "vibration": vibration_value}[args.setting](args.value)
        except (argparse.ArgumentTypeError, ValueError) as e:
            ap.error(f"set {args.setting}: {e}")

    if args.dry_run and args.cmd == "restore":
        ap.error("restore needs the controller's current state: use --plan instead")
    if args.dry_run and args.cmd == "set" and args.setting == "map" \
            and args.value[0] in BACK_KEYS:
        ap.error("L4/R4 writes depend on the button's macro: use --plan instead of --dry-run")
    if args.dry_run and args.cmd == "set":
        if args.setting in LIGHT_SETTINGS and args.effect is None:
            ap.error("--dry-run needs --effect (the active one is read from the device)")
        if args.setting not in LIGHT_SETTINGS and args.profile is None:
            ap.error("--dry-run needs --profile (the active one is read from the device)")
    if args.cmd == "gui":
        from .gui import main as gui_main
        return gui_main(no_browser=args.no_browser)
    if args.cmd == "rumble":
        if args.dry_run:
            print("dry run: rumble sends no vendor-channel packets; it plays a kernel "
                  "force-feedback effect on the controller's XInput event device")
            return 0
        devices = find()
        if len(devices) != 1:
            print(f"error: need exactly one Cyclone 2, found {devices or 'none'}",
                  file=sys.stderr)
            return 1
        cmd_rumble(devices[0][0], args)
        return 0
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
