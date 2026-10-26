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
