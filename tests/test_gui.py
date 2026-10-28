import http.client
import json
import threading
import unittest
from pathlib import Path

from cyclone2 import gui, protocol as P
from cyclone2.cli import load_backup
from cyclone2.device import DeviceError
from cyclone2.settings import policy

ROOT = Path(__file__).resolve().parent.parent


class FakeDev:
    """Enough of Cyclone2 for the GUI: reads/writes against backup data."""

    def __init__(self):
        self.banks = {b: bytearray(v) for b, v in
                      load_backup(ROOT / "backups/2026-10-26-dongle.json").items()}
        self.profile = 1
        self.writes = []
        self.policy = policy
        self.path = "/dev/hidraw-fake"

    def fw_version(self):
        return {"controller": {"raw": "0346", "version": "3.4.6"},
                "dongle": {"raw": "", "version": None}}

    def current_profile(self):
        return self.profile

    def read(self, bank, offset, length):
        return bytes(self.banks[bank][offset:offset + length])

    def write(self, bank, offset, data):
        why = self.policy(P.write_chunk(bank, offset, data))
        assert why is None, why
        self.writes.append((bank, offset, bytes(data)))
        self.banks[bank][offset:offset + len(data)] = data

    def switch_profile(self, n):
        self.profile = n

    def status(self):
        return {"battery": 87, "charging": False, "charge_state": 0}

    def close(self):
        pass


class Plans(unittest.TestCase):
    def plan(self, req):
        return gui.plan_settings(FakeDev(), 0x1053, {"profile": 1, **req}).items

    def test_state_reads_known_values(self):
        s = gui.read_state(FakeDev(), 0x1053)
        self.assertEqual(s["battery"], {"percent": 87, "charging": False})
        self.assertEqual(s["sticks"]["left"], {"enabled": True, "inner": 10, "outer": 100, "shape": "circle"})
        self.assertEqual(s["triggers"]["lt"]["hair"], "off")
        self.assertEqual(s["vibration"], {"left": 4, "right": 4})
        self.assertEqual(s["light"]["colors"]["right"], "#ff0500")
        self.assertEqual(s["map"]["A"], "A")

    def test_no_change_no_writes(self):
        s = gui.read_state(FakeDev(), 0x1053)
        req = {"light": {"brightness": s["light"]["brightness"]},
               "sticks": {"left": {"inner": 10, "outer": 100}},
               "triggers": {"lt": {"hair": "off"}}, "map": {"A": "A"},
               "vibration": {"left": 4, "right": 4}}
        self.assertEqual(self.plan(req), [])

    def test_packets(self):
        items = self.plan({"sticks": {"left": {"inner": 15}},
                           "triggers": {"lt": {"hair": "adaptive"}},
                           "map": {"A": "B"}, "vibration": {"left": 3},
                           "light": {"brightness": 50, "colors": {"right": "#00ff00"}}})
        packets = {i["packet"] for i in items}
        self.assertEqual(packets, {
            "0f 03 20 01 78 01 32", "0f 03 20 01 7c 03 00 ff 00",
            "0f 03 01 02 29 02 0f 64", "0f 03 01 01 fa 01 81",
            "0f 03 01 00 20 02 4b 64", "0f 03 01 00 7a 02 01 0a"})

    def test_validation(self):
        for req in ({"sticks": {"left": {"inner": 100}}},
                    {"light": {"colors": {"zone2": "#000000"}}},
                    {"map": {"A": "KB_ESC"}}, {"vibration": {"left": 5}},
                    {"triggers": {"lt": {"hair": "fixed"}}}):
            with self.assertRaises(DeviceError, msg=req):
                self.plan(req)

    def test_apply_verifies_and_writes(self):
        s = gui.Session()
        s.dev, s.pid = FakeDev(), 0x1053
        plan = s.run(lambda d, pid: gui.plan_settings(d, pid, {
            "profile": 1, "sticks": {"right": {"inner": 12}}}))
        pid = s.store(plan)["id"]
        self.assertEqual(s.apply(pid), 1)
        self.assertEqual(s.dev.writes, [(1, 0x249, bytes([12, 100]))])
        with self.assertRaises(DeviceError):   # a plan can only be applied once
            s.apply(pid)

    def test_apply_refuses_if_controller_changed(self):
        s = gui.Session()
        s.dev, s.pid = FakeDev(), 0x1053
        plan = s.run(lambda d, pid: gui.plan_settings(d, pid, {
            "profile": 1, "sticks": {"right": {"inner": 12}}}))
        pid = s.store(plan)["id"]
        dev = s.dev
        dev.banks[1][0x249] = 33                   # changed behind our back
        with self.assertRaises(DeviceError):
            s.apply(pid)
        self.assertEqual(dev.writes, [])


class ServerSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = gui.Server()
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()

    def test_page_needs_token(self):
        self.assertEqual(self.req("GET", "/")[0], 403)
        self.assertEqual(self.req("GET", "/?t=wrong")[0], 403)
        status, body = self.req("GET", f"/?t={self.srv.token}")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Cyclone 2</title>", body)

    def test_api_needs_header_token_and_local_host(self):
        ok = {"Content-Type": "application/json", "X-Cyclone2-Token": self.srv.token}
        self.assertEqual(self.req("POST", "/api/ping", ok, b"{}")[0], 200)
        self.assertEqual(self.req("POST", "/api/ping", {**ok, "X-Cyclone2-Token": "x"}, b"{}")[0], 403)
        self.assertEqual(self.req("POST", "/api/ping", {**ok, "Host": "evil.example"}, b"{}")[0], 403)
        self.assertEqual(self.req("POST", "/api/ping", {"Content-Type": "application/json"}, b"{}")[0], 403)
        self.assertEqual(self.req("POST", "/api/ping", {**ok, "Content-Type": "text/plain"}, b"{}")[0], 400)


if __name__ == "__main__":
    unittest.main()


class BackButtonPlans(unittest.TestCase):
    def test_state_and_plan(self):
        dev = FakeDev()
        s = gui.read_state(dev, 0x1053)
        self.assertEqual(s["map"]["L4"], "L4")
        self.assertEqual([m["key"] for m in s["macro"]["L4"]], ["RB", "Home"])
        self.assertEqual((s["map"]["R4"], s["macro"]["R4"]), ("R4", None))
        items = gui.plan_settings(dev, 0x1053, {"profile": 1, "map": {"L4": "B", "R4": "R4"}}).items
        self.assertEqual([i["packet"] for i in items],
                         ["0f 03 01 00 b7 01 00", "0f 03 01 00 ba 09 " + "00 " * 8 + "00",
                          "0f 03 01 00 b2 02 01 0a"])
        self.assertEqual(items[2]["label"], "L4 → B")

    def test_l4_itself_with_macro_still_clears_macro(self):
        items = gui.plan_settings(FakeDev(), 0x1053, {"profile": 1, "map": {"L4": "L4"}}).items
        self.assertEqual([i["packet"][:17] for i in items],
                         ["0f 03 01 00 b7 01", "0f 03 01 00 ba 09"])
        self.assertEqual(items[0]["label"], "L4: turn off its macro")


class ShapePlans(unittest.TestCase):
    def test_shape(self):
        s = gui.read_state(FakeDev(), 0x1053)
        self.assertEqual(s["sticks"]["left"]["shape"], "circle")
        items = gui.plan_settings(FakeDev(), 0x1053, {"profile": 1,
                                  "sticks": {"right": {"shape": "square"}}}).items
        self.assertEqual([i["packet"] for i in items], ["0f 03 01 02 47 01 01"])


class RatePlans(unittest.TestCase):
    def test_rate(self):
        s = gui.read_state(FakeDev(), 0x1053)
        self.assertEqual(s["report_rate"], 1000)
        items = gui.plan_settings(FakeDev(), 0x1053, {"profile": 1, "report_rate": 250}).items
        self.assertEqual([i["packet"] for i in items], ["0f 03 01 00 2e 01 00"])
        with self.assertRaises(DeviceError):
            gui.plan_settings(FakeDev(), 0x1053, {"profile": 1, "report_rate": 125})
