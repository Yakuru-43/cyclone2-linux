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
        self.assertEqual([f.offset for f in APPROVED_WRITES[:5]],
                         [0x004, 0x080, 0x0FC, 0x178, 0x1F4])

    def test_color_write_matches_gsc(self):
        # capture 02: GSC zone 3 (right grip) = effect 4 frame 1 data zone 1
        from cyclone2.layout import light_color
        f = light_color(3, 0, 1)
        self.assertEqual(f.offset, 0x17C)
        pkt = P.write_chunk(f.bank, f.offset, bytes.fromhex("00ff11"))
        self.assertEqual(pkt[:9].hex(" "), "0f 03 20 01 7c 03 00 ff 11")
        self.assertIsNone(policy(pkt))
        self.assertIsNotNone(policy(P.write_chunk(0x20, 0x17D, b"\0\0\0")))  # misaligned


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
        planned = planned_packets(Namespace(cmd="set", setting="brightness", effect=3, value=50))
        self.assertEqual(planned, sent)


class FakeDevice:
    """Records the packets Cyclone2's methods would send; data from a backup."""

    def __init__(self, banks, profile=1):
        self.banks = {b: bytearray(v) for b, v in banks.items()}
        self.profile = profile
        self.sent = []

    def current_profile(self):
        self.sent.append(P.current_profile())
        return self.profile

    def read(self, bank, offset, length):
        self.sent.append(P.read_chunk(bank, offset, length))
        return bytes(self.banks[bank][offset:offset + length])

    def write(self, bank, offset, data):
        self.sent.append(P.write_chunk(bank, offset, data))
        self.banks[bank][offset:offset + len(data)] = data
        return self.read(bank, offset, len(data))


class Deadzone(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        from cyclone2.cli import load_backup
        self.banks = load_backup(Path(__file__).resolve().parent.parent
                                 / "backups/2026-10-26-cli.json")

    def run_set(self, value, profile=None, setting="deadzone"):
        from argparse import Namespace
        from cyclone2.cli import cmd_set, deadzone_value, hair_trigger_value, planned_packets
        from cyclone2.cli import map_value
        parse = {"deadzone": deadzone_value, "hair-trigger": hair_trigger_value,
                 "map": map_value}[setting]
        args = Namespace(cmd="set", setting=setting, value=parse(value), profile=profile)
        dev = FakeDevice(self.banks)
        cmd_set(dev, None, None, args)
        planned = planned_packets(Namespace(**{**vars(args), "profile": profile or 1}))
        return dev, planned

    def test_dry_run_equals_what_cmd_sends(self):
        for value in ("left=15", "right=12:95"):
            dev, planned = self.run_set(value)
            self.assertEqual(dev.sent, planned, value)
            for pkt in dev.sent:
                self.assertIsNone(policy(pkt), pkt[:8].hex(" "))

    def test_left_inner_matches_capture_03(self):
        dev, _ = self.run_set("left=20")
        self.assertEqual(dev.sent[2][:7].hex(" "), "0f 03 01 02 29 01 14")

    def test_refuses_inner_above_outer(self):
        from cyclone2.device import DeviceError
        with self.assertRaises(DeviceError):
            self.run_set("left=100")


class Triggers(Deadzone):
    def test_trigger_dry_runs_equal_sent(self):
        for setting, value in (("deadzone", "lt=50"), ("deadzone", "rt=10:90"),
                               ("hair-trigger", "lt=adaptive"), ("hair-trigger", "rt=off")):
            dev, planned = self.run_set(value, setting=setting)
            self.assertEqual(dev.sent, planned, value)
            for pkt in dev.sent:
                self.assertIsNone(policy(pkt), pkt[:8].hex(" "))

    def test_matches_gsc_captures(self):
        dev, _ = self.run_set("lt=15")                                  # capture 04
        self.assertEqual(dev.sent[2][:7].hex(" "), "0f 03 01 01 f1 01 0f")
        dev, _ = self.run_set("lt=adaptive", setting="hair-trigger")    # capture 05
        self.assertEqual(dev.sent[2][:7].hex(" "), "0f 03 01 01 fa 01 81")

    def test_unconfirmed_hair_values_refused(self):
        for v in (0x82, 0x01, 0x80, 0xFF):
            self.assertIsNotNone(policy(P.write_chunk(1, 0x1FA, [v])), hex(v))


class Remap(Deadzone):
    def test_matches_capture_06(self):
        dev, planned = self.run_set("A=B", setting="map")
        self.assertEqual(dev.sent, planned)
        self.assertEqual(dev.sent[2][:8].hex(" "), "0f 03 01 00 7a 02 01 0a")
        dev, planned = self.run_set("A=A", setting="map")
        self.assertEqual(dev.sent[2][:8].hex(" "), "0f 03 01 00 7a 02 00 00")
        for pkt in dev.sent:
            self.assertIsNone(policy(pkt))

    def test_keyboard_targets_refused_for_now(self):
        self.assertIsNotNone(policy(P.write_chunk(1, 0x07A, [1, 0x32])))   # KB_ESC
        self.assertIsNotNone(policy(P.write_chunk(1, 0x079, [1, 0x0A, 0])))  # spills into turbo


class SwitchProfile(unittest.TestCase):
    def test_policy(self):
        self.assertIsNone(policy(P.switch_profile(2)))
        for pkt in (P.switch_profile(5), P.switch_profile(0), P.packet(0x07, 2, 1)):
            self.assertIsNotNone(policy(pkt), pkt[:4].hex(" "))

    def test_matches_capture_04(self):
        self.assertEqual(P.switch_profile(2)[:3].hex(" "), "0f 07 02")
