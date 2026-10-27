import json
import unittest
from pathlib import Path

from cyclone2 import protocol as P
from cyclone2.layout import BANK_SIZE

ROOT = Path(__file__).resolve().parent.parent


class Packets(unittest.TestCase):
    def test_builders_are_full_reports(self):
        for pkt in [P.fw_version(), P.current_profile(), P.heartbeat(),
                    *P.read_bank(1), *P.read_bank(0x20)]:
            self.assertEqual(len(pkt), 64)
            self.assertIsNone(P.check_read_only(pkt), pkt[:6].hex())

    def test_read_bank_matches_gsc(self):
        # GSC's connect reads in capture 00 are exactly these packets
        gsc = [bytes.fromhex(json.loads(l)["hex"])
               for l in open(ROOT / "captures/00-connect-baseline.jsonl")
               if json.loads(l)["dir"] == "out" and json.loads(l).get("op") == "ReadProfile"]
        ours = [p for b in (1, 2, 3, 4, 5, 0x20) for p in P.read_bank(b)]
        self.assertEqual(gsc, ours)

    def test_read_only_refuses_writes_and_danger(self):
        for pkt in [P.packet(0x03, 1, 0, 0x2E, 1, 2), P.packet(0x07, 1),
                    P.packet(0x17, 0x55, 0x88), P.packet(0xFC), P.packet(0xFD, 1),
                    P.packet(0x20, 0x66, 0x55, 10, 10), P.packet(0x0D),
                    P.packet(0x04, 1, 0x02, 0xA0, 58),       # past the end of bank 1
                    P.packet(0x04, 0x30, 0, 0, 1),           # unknown bank size
                    P.packet(0x04, 9, 0, 0, 1), P.packet(0x09, 1),
                    bytes([0x0F, 0x09])]:                   # not padded to 64
            self.assertIsNotNone(P.check_read_only(pkt), pkt[:6].hex())

    def test_parse_fw_version_from_capture(self):
        reply = bytes.fromhex("10 0a 30 00 33 00 34 00 36 00".replace(" ", "")).ljust(64, b"\0")
        fw = P.parse_fw_version(reply)
        self.assertEqual(fw["controller"], {"raw": "0346", "version": "3.4.6"})
        self.assertEqual(fw["dongle"], {"raw": "", "version": None})

    def test_bank_sizes(self):
        self.assertEqual(len(P.read_bank(1)), 12)
        self.assertEqual(len(P.read_bank(0x20)), 11)
        self.assertEqual(sum(p[5] for p in P.read_bank(1)), BANK_SIZE[1])


if __name__ == "__main__":
    unittest.main()


class Status(unittest.TestCase):
    def test_parse_status(self):
        report = bytearray(64)
        report[0], report[35], report[36] = 0x12, 1, 87
        self.assertEqual(P.parse_status(bytes(report)),
                         {"battery": 87, "charging": True, "charge_state": 1})
        report[35] = 0
        self.assertFalse(P.parse_status(bytes(report))["charging"])

    def test_rejects_other_reports(self):
        with self.assertRaises(ValueError):
            P.parse_status(bytes([0x10]) + bytes(63))

    def test_heartbeat_is_allowed_read_only(self):
        self.assertEqual(P.heartbeat()[:3].hex(" "), "0f f2 00")
        self.assertIsNone(P.check_read_only(P.heartbeat()))
