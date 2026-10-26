import unittest

from cyclone2 import protocol as P
from cyclone2.cli import APPROVED_WRITES, policy
from cyclone2.layout import light_brightness


class Writes(unittest.TestCase):
    def test_brightness_write_matches_gsc(self):
        # capture 01: GSC set effect 4 (index 3) brightness to 50
        f = light_brightness(3)
        self.assertEqual(P.write_chunk(f.bank, f.offset, [50])[:7].hex(" "),
                         "0f 03 20 01 78 01 32")
        self.assertIsNone(policy(P.write_chunk(f.bank, f.offset, [50])))

    def test_only_approved_fields(self):
        for pkt in [P.write_chunk(0x20, 0x178, [101]),        # out of range
                    P.write_chunk(0x20, 0x177, [1]),          # speed, not approved
                    P.write_chunk(0x20, 0x178, [1, 1]),       # spills past the field
                    P.write_chunk(1, 0x2E, [2]),              # report rate, not approved
                    P.write_chunk(1, 0x39E, [1] * 6),         # device ID
                    P.packet(0x17, 0x55, 0x88), P.packet(0xFC), P.packet(0x07, 1)]:
            self.assertIsNotNone(policy(pkt), pkt[:7].hex(" "))

    def test_reads_still_allowed(self):
        self.assertIsNone(policy(P.read_chunk(0x20, 0, 1)))
        self.assertIsNone(policy(P.fw_version()))

    def test_approved_list(self):
        self.assertEqual([f.offset for f in APPROVED_WRITES],
                         [0x004, 0x080, 0x0FC, 0x178, 0x1F4])


if __name__ == "__main__":
    unittest.main()


class Restore(unittest.TestCase):
    def test_diff_writes_minimal(self):
        cur = bytearray(680)
        tgt = bytearray(680)
        tgt[0x2E] = 2
        tgt[0x100:0x150] = b"\x01" * 0x50   # 80 bytes -> two chunks
        chunks = P.diff_writes(1, cur, tgt)
        self.assertEqual(chunks[0], (1, 0x2E, b"\x02"))
        self.assertEqual([(o, len(d)) for _, o, d in chunks[1:]], [(0x100, 58), (0x13A, 22)])
        for bank, off, data in chunks:
            self.assertIsNone(P.check_restore(P.write_chunk(bank, off, data)))

    def test_diff_writes_none_when_equal(self):
        self.assertEqual(P.diff_writes(1, bytes(680), bytes(680)), [])

    def test_restore_policy_bounds(self):
        self.assertIsNotNone(P.check_restore(P.write_chunk(1, 0x2A0, b"\0" * 9)))
        self.assertIsNotNone(P.check_restore(P.write_chunk(0x30, 0, b"\1")))
        self.assertIsNotNone(P.check_restore(P.packet(0x17, 0x55, 0x88)))


class DryRunMatchesDevice(unittest.TestCase):
    def test_set_dry_run_equals_logged_packets(self):
        # captures/12: what `set brightness 50` actually sent on the device
        import json
        from argparse import Namespace
        from pathlib import Path
        from cyclone2.cli import planned_packets
        log = Path(__file__).resolve().parent.parent / "captures/12-cli-set-brightness.jsonl"
        sent = [bytes.fromhex(json.loads(l)["hex"]) for l in open(log)
                if json.loads(l)["dir"] == "out"][:4]
        planned = planned_packets(Namespace(cmd="set", effect=3, value=50))
        self.assertEqual(planned, sent)
