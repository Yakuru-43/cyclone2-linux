"""cyclone2 gui: a small local web UI, standard library only.

The page is served on 127.0.0.1 and opened in a browser that is already
installed. Every request needs the session token and a local Host header.
Changes are never written directly: the page asks for a plan (computed from
fresh reads, checked against the same allow-list as the CLI), shows the exact
packets, and only that reviewed plan can be applied. Every write is read back.
"""

import http.server
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from . import protocol as P
from .device import CONFIG_PIDS, Cyclone2, DeviceError, find, read_range
from .keycodes import KEY_NAMES
from .layout import (BACK_BLOCK, BACK_KEYS, BACK_SELF, HAIR_TRIGGER_MODES, KEYS, LIGHT_BANK,
                     LIGHT_ZONES, MOTOR_RAW, PROFILE_BANKS, STICKS, TRIGGERS, back_map_writes,
                     decode_back, hair_trigger, key_map, light_brightness, light_color,
                     motors, stick_deadzone, trigger_deadzone)
from .inputs import AxisReader
from .rumble import find_event_node, rumble
from .settings import MAP_TARGETS, policy

ZONE_LABELS = {"left": "Left grip", "right": "Right grip", "m": "Dot above M", "home": "Home"}
LEVEL_OF = {raw: level for level, raw in MOTOR_RAW.items()}
IDLE_EXIT = 20         # seconds without a page before the server stops
FIRST_PAGE_WAIT = 300  # seconds to wait for the browser to open the page
MAX_BODY = 256 * 1024


def hair_mode(status):
    if not status & 0x80:
        return "off"
    return "adaptive" if status == 0x81 else f"unsupported (0x{status:02x})"


def battery(dev):
    """{'battery': %, 'charging': bool} or None if the controller did not say."""
    try:
        st = dev.status()
    except DeviceError:
        return None
    return {"percent": st["battery"], "charging": st["charging"]}


def read_state(dev, pid, profile=None):
    return _read(dev, pid, profile)[0]


def _read(dev, pid, profile=None):
    """(state for the page, raw profile bytes, raw light animation header+frame 1)."""
    if profile is not None:
        profile = _int(profile, 1, 4, "profile")
    fw = dev.fw_version()
    active = dev.current_profile()
    bank = profile or active
    p = bytearray(680)
    p[0x020:0x266] = read_range(dev, bank, 0x020, 0x266 - 0x020)  # motors .. sticks
    effect = dev.read(LIGHT_BANK, 0, 1)[0]
    effect = effect if effect <= 3 else 0            # GSC reads >3 as 0
    anim = read_range(dev, LIGHT_BANK, 1 + 124 * effect, 4 + 15)  # header + frame 1

    def deadzone(f):
        return {"enabled": bool(p[f.offset - 1]), "inner": p[f.offset], "outer": p[f.offset + 1]}

    def mapping(key):
        f = key_map(bank, key)
        en, code = p[f.offset], p[f.offset + 1]
        if not en:
            return key
        return KEY_NAMES.get(code) if code in MAP_TARGETS.values() else None

    state = {
        "device": {"connection": CONFIG_PIDS[pid],
                   "firmware": fw["controller"]["version"],
                   "dongle_firmware": fw["dongle"]["version"]},
        "active_profile": active,
        "profile": bank,
        "light": {"effect": effect, "brightness": anim[3],
                  "colors": {z: "#" + anim[4 + 3 * LIGHT_ZONES[z]:7 + 3 * LIGHT_ZONES[z]].hex()
                             for z in ZONE_LABELS}},
        "sticks": {s: deadzone(stick_deadzone(bank, s)) for s in STICKS},
        "triggers": {t: {**deadzone(trigger_deadzone(bank, t)),
                         "hair": hair_mode(p[hair_trigger(bank, t).offset])} for t in TRIGGERS},
        "vibration": {side: LEVEL_OF.get(p[0x020 + i]) for i, side in enumerate(("left", "right"))},
        "map": {k: mapping(k) for k in KEYS},
        "macro": {},
        "map_targets": list(MAP_TARGETS),
        "zones": ZONE_LABELS,
    }
    for key, base in BACK_KEYS.items():
        state["map"][key], state["macro"][key] = decode_back(p[base:base + BACK_BLOCK], key,
                                                            KEY_NAMES)
    state["battery"] = battery(dev)
    return state, bytes(p), anim


class Plan:
    """Exact writes computed from fresh reads; applied only after review."""

    def __init__(self, kind="settings"):
        self.id = secrets.token_hex(8)
        self.kind = kind
        self.items = []

    def write(self, label, bank, offset, old, new, check=policy):
        new = bytes(new)
        if bytes(old) == new:
            return
        pkt = P.write_chunk(bank, offset, new)
        why = check(pkt)
        if why:
            raise DeviceError(f"{label}: refused ({why})")
        self.items.append({"label": label, "kind": "write", "bank": bank, "offset": offset,
                           "old": bytes(old).hex(), "new": new.hex(),
                           "packet": pkt[:6 + len(new)].hex(" ")})

    def switch(self, n):
        pkt = P.switch_profile(n)
        why = policy(pkt)
        if why:
            raise DeviceError(why)
        self.items.append({"label": f"Make profile {n} active", "kind": "switch",
                           "profile": n, "packet": pkt[:3].hex(" ")})

    def public(self):
        return {"id": self.id, "items": [{k: v for k, v in i.items()
                                          if k in ("label", "packet")} for i in self.items]}


def _int(v, lo, hi, what):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise DeviceError(f"{what} must be {lo}-{hi}")
    return v


def plan_settings(dev, pid, req):
    bank = _int(req.get("profile"), 1, 4, "profile")
    cur, p, anim = _read(dev, pid, bank)
    plan = Plan()

    def raw(f):
        return p[f.offset:f.offset + f.length]

    light = req.get("light", {})
    effect = cur["light"]["effect"]
    if "brightness" in light:
        f = light_brightness(effect)
        plan.write(f"Lighting brightness → {light['brightness']}", f.bank, f.offset,
                   anim[3:4], [_int(light["brightness"], 0, 100, "brightness")])
    for zone, color in light.get("colors", {}).items():
        if zone not in ZONE_LABELS or not (isinstance(color, str) and len(color) == 7
                                           and color[0] == "#"):
            raise DeviceError(f"bad colour for {zone}")
        z = LIGHT_ZONES[zone]
        f = light_color(effect, 0, z)
        plan.write(f"{ZONE_LABELS[zone]} colour → {color}", f.bank, f.offset,
                   anim[4 + 3 * z:7 + 3 * z], bytes.fromhex(color[1:]))

    labels = {"left": "Left stick", "right": "Right stick", "lt": "LT", "rt": "RT"}
    for group, field_of, names in (("sticks", stick_deadzone, STICKS),
                                   ("triggers", trigger_deadzone, TRIGGERS)):
        for name, want in req.get(group, {}).items():
            if name not in names:
                raise DeviceError(f"unknown {group[:-1]} {name}")
            have, label = cur[group][name], labels[name]
            if "inner" in want or "outer" in want:
                inner = _int(want.get("inner", have["inner"]), 0, 100, "inner deadzone")
                outer = _int(want.get("outer", have["outer"]), 0, 100, "outer deadzone")
                if not inner < outer:
                    raise DeviceError(f"{label}: inner deadzone must be below outer")
                if not have["enabled"]:
                    raise DeviceError(f"{label}: deadzone is disabled in this profile")
                f = field_of(bank, name)
                plan.write(f"{label} deadzone → {inner}–{outer}", bank, f.offset, raw(f),
                           [inner, outer])
            if "hair" in want and want["hair"] != have["hair"]:
                if want["hair"] not in HAIR_TRIGGER_MODES:
                    raise DeviceError(f"{label}: hair trigger must be off or adaptive")
                if have["hair"] not in HAIR_TRIGGER_MODES:
                    raise DeviceError(f"{label}: current hair-trigger mode is not supported")
                f = hair_trigger(bank, name)
                plan.write(f"{label} hair trigger → {want['hair']}", bank, f.offset, raw(f),
                           [HAIR_TRIGGER_MODES[want["hair"]]])

    vib = req.get("vibration", {})
    if vib:
        levels = [vib.get(side, cur["vibration"][side]) for side in ("left", "right")]
        if None in levels:
            raise DeviceError("the current vibration level is not a standard one; set both motors")
        levels = [_int(v, 0, 4, "vibration level") for v in levels]
        f = motors(bank)
        plan.write(f"Vibration → left {levels[0]}, right {levels[1]}", bank, f.offset, raw(f),
                   [MOTOR_RAW[v] for v in levels])

    for key, target in req.get("map", {}).items():
        if key in BACK_KEYS:
            if target != key and target not in MAP_TARGETS:
                raise DeviceError(f"{key}: unsupported target {target}")
            if target == cur["map"][key] and not cur["macro"][key]:
                continue
            base = BACK_KEYS[key]
            code = BACK_SELF[key] if target == key else MAP_TARGETS[target]
            name = "nothing" if target == key else target
            for label, off, old, new in back_map_writes(bank, key, p[base:base + BACK_BLOCK], code):
                plan.write(label.replace("mapping", name), bank, off, old, new)
            continue
        if key not in KEYS:
            raise DeviceError(f"unknown button {key}")
        if target != key and target not in MAP_TARGETS:
            raise DeviceError(f"{key}: unsupported target {target}")
        if target == cur["map"][key]:
            continue
        f = key_map(bank, key)
        plan.write(f"{key} → {target}", bank, f.offset, raw(f),
                   b"\0\0" if target == key else bytes([1, MAP_TARGETS[target]]))
    return plan


class Session:
    """The open controller, shared by request threads, one request at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dev = None
        self.pid = None
        self.plans = {}
        self.axes = None
        self.axes_lock = threading.Lock()

    def sticks(self):
        """What games see on the xpad device (read-only; no packets sent)."""
        with self.axes_lock:
            if self.axes and self.axes.error:
                self.axes = None
            if self.axes is None:
                found = find()
                if len(found) != 1:
                    raise DeviceError("Controller not found.")
                self.axes = AxisReader(find_event_node(found[0][0]))
            return self.axes.snapshot()

    def _close(self):
        if self.dev:
            self.dev.close()
        self.dev = None

    def _open(self):
        if self.dev:
            return
        found = find()
        if len(found) != 1:
            raise DeviceError("Controller not found. Connect it by cable or dongle in XInput "
                              "mode (holding M shows green)." if not found else
                              "More than one Cyclone 2 connected.")
        path, self.pid = found[0]
        self.dev = Cyclone2(path, policy=policy, dongle=CONFIG_PIDS[self.pid] == "dongle")

    def run(self, fn, retry=True):
        with self.lock:
            for attempt in (1, 2):
                try:
                    self._open()
                    return fn(self.dev, self.pid)
                except OSError as e:          # unplugged / re-enumerated
                    self._close()
                    if attempt == 2 or not retry:
                        raise DeviceError(f"Controller connection lost ({e.strerror}).") from None
                except DeviceError:
                    if not self.dev or not find():
                        self._close()
                    raise

    def store(self, plan):
        self.plans = {plan.id: plan}   # only the latest reviewed plan can be applied
        return plan.public()

    def apply(self, plan_id):
        plan = self.plans.pop(plan_id, None)
        if plan is None:
            raise DeviceError("That plan is no longer valid; review the changes again.")

        def go(dev, pid):
            if plan.kind == "restore":
                dev.policy = lambda pkt: (P.check_restore(pkt) if pkt[1] == P.OP_WRITE
                                          else P.check_read_only(pkt))
            try:
                todo = []
                for item in plan.items:          # nothing may have changed since review
                    if item["kind"] == "write":
                        now = dev.read(item["bank"], item["offset"], len(item["new"]) // 2).hex()
                        if now not in (item["old"], item["new"]):
                            raise DeviceError("The controller changed since you reviewed "
                                              "these changes; nothing was written. Reload.")
                        if now == item["new"]:
                            continue
                    todo.append(item)
                for item in todo:
                    if item["kind"] == "switch":
                        dev.switch_profile(item["profile"])
                    else:
                        dev.write(item["bank"], item["offset"], bytes.fromhex(item["new"]))
            finally:
                dev.policy = policy
            return len(plan.items)
        return self.run(go, retry=False)


def plan_restore(dev, pid, doc):
    banks = doc.get("banks") if isinstance(doc, dict) else None
    if not isinstance(banks, dict):
        raise DeviceError("not a cyclone2 backup file")
    plan = Plan("restore")
    for key, hexdata in banks.items():
        bank = int(key, 16)
        target = bytes.fromhex(hexdata)
        if bank not in (*PROFILE_BANKS, LIGHT_BANK) or len(target) != P.BANK_SIZE[bank]:
            raise DeviceError(f"backup bank {key} is invalid")
        current = dev.read_bank(bank)
        for b, off, data in P.diff_writes(bank, current, target):
            plan.write(f"Restore bank 0x{b:02x} +0x{off:03x} ({len(data)} bytes)", b, off,
                       current[off:off + len(data)], data, check=P.check_restore)
    return plan


def backup(dev, pid):
    fw = dev.fw_version()
    return {"format": "cyclone2-backup/1",
            "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "device": f"3537:{pid:04x} ({CONFIG_PIDS[pid]})",
            "firmware": {k: v["raw"] for k, v in fw.items()},
            "banks": {f"0x{b:02x}": dev.read_bank(b).hex() for b in (*PROFILE_BANKS, LIGHT_BANK)}}


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.token = secrets.token_urlsafe(24)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.url = f"http://127.0.0.1:{port}/?t={self.token}"
        self.session = Session()
        self.last_seen = None
        self.started = time.monotonic()
        self.page = resources.files("cyclone2").joinpath("gui.html").read_bytes()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "cyclone2"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self, api):
        if self.headers.get("Host") not in self.server.allowed_hosts:
            return False
        token = (self.headers.get("X-Cyclone2-Token", "") if api else
                 parse_qs(urlsplit(self.path).query).get("t", [""])[0])
        return secrets.compare_digest(token, self.server.token)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            if not self._allowed(api=False):
                return self._send(403, b"Open the link printed by `cyclone2 gui`.", "text/plain")
            return self._send(200, self.server.page, "text/html; charset=utf-8", [(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'")])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._allowed(api=True):
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY or self.headers.get("Content-Type") != "application/json":
            return self._send(400, {"error": "bad request"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        self.server.last_seen = time.monotonic()
        s = self.server.session
        route = urlsplit(self.path).path
        try:
            if route == "/api/ping":
                result = {"ok": True}
            elif route == "/api/state":
                result = s.run(lambda d, pid: read_state(d, pid, body.get("profile")))
            elif route == "/api/plan":
                result = s.store(s.run(lambda d, pid: plan_settings(d, pid, body)))
            elif route == "/api/plan-profile":
                plan = Plan()
                plan.switch(_int(body.get("profile"), 1, 4, "profile"))
                result = s.store(plan)
            elif route == "/api/plan-restore":
                result = s.store(s.run(lambda d, pid: plan_restore(d, pid, body)))
            elif route == "/api/apply":
                result = {"applied": s.apply(str(body.get("id", "")))}
            elif route == "/api/sticks":
                result = s.sticks()
            elif route == "/api/battery":
                result = s.run(lambda d, pid: {"battery": battery(d),
                                               "connection": CONFIG_PIDS[pid]})
            elif route == "/api/backup":
                result = s.run(backup)
            elif route == "/api/rumble":
                result = s.run(lambda d, pid: rumble(find_event_node(d.path), 100, 100, 700)
                               or {"ok": True})
            else:
                return self._send(404, {"error": "not found"})
        except DeviceError as e:
            return self._send(409, {"error": str(e)})
        except (OSError, ValueError) as e:
            return self._send(409, {"error": str(e)})
        self._send(200, result)


def open_window(url):
    """A chromeless app window if a Chromium-based browser exists, else the
    default browser."""
    for cmd in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                "brave-browser", "microsoft-edge"):
        if shutil.which(cmd):
            subprocess.Popen([cmd, f"--app={url}"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
    if shutil.which("flatpak"):
        for app in ("org.chromium.Chromium", "com.google.Chrome", "com.brave.Browser",
                    "com.microsoft.Edge"):
            if subprocess.run(["flatpak", "info", app], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                subprocess.Popen(["flatpak", "run", app, f"--app={url}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return
    webbrowser.open(url)


def main(no_browser=False):
    server = Server()
    print(f"cyclone2 GUI: {server.url}", flush=True)
    print("It stops by itself shortly after you close the page (or press Ctrl-C).", flush=True)

    def watchdog():
        while True:
            time.sleep(2)
            now = time.monotonic()
            idle = (now - server.last_seen > IDLE_EXIT) if server.last_seen else \
                (now - server.started > FIRST_PAGE_WAIT)
            if idle:
                server.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    if not no_browser:
        open_window(server.url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.session._close()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
