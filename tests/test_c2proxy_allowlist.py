import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "c2proxy", Path(__file__).resolve().parent.parent / "tools" / "c2proxy.py")
c2proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c2proxy)


def pkt(*b):
    return bytes(b).ljust(64, b"\0")


class AllowList(unittest.TestCase):
    def test_reads_and_volatile_pass(self):
        for p in [pkt(0x0F, 0x09), pkt(0x0F, 0x0B), pkt(0x0F, 0x04, 1, 0, 0, 58),
                  pkt(0x0F, 0x10, 0x20, 0, 5, 3), pkt(0x0F, 0xF2, 0),
                  pkt(0x0F, 0x20, 0x66, 0x55, 10, 10), pkt(0x0F, 0x0D)]:
            self.assertIsNone(c2proxy.allowed(p), p[:6].hex())

    def test_firmware_and_calibration_blocked(self):
        for p in [pkt(0x0F, 0x17, 0x55, 0x88), pkt(0x0F, 0xFC), pkt(0x0F, 0xFD, 1),
                  pkt(0x0F, 0xF0), pkt(0x0F, 0x08), pkt(0x0F, 0x12), pkt(0x0F, 0x14),
                  pkt(0x0F, 0x01), pkt(0x0F, 0x02, 0), pkt(0x0F, 0x99)]:
            self.assertIsNotNone(c2proxy.allowed(p), p[:4].hex())

    def test_other_report_ids_blocked(self):
        self.assertIsNotNone(c2proxy.allowed(pkt(0x41, 0x09)))
        self.assertIsNotNone(c2proxy.allowed(pkt(0x03, 0x01)))
        self.assertIsNotNone(c2proxy.allowed(b"\x0f"))

    def test_profile_writes(self):
        self.assertIsNone(c2proxy.allowed(pkt(0x0F, 0x03, 1, 0x02, 0x29, 2, 15, 100)))
        self.assertIsNone(c2proxy.allowed(pkt(0x0F, 0x03, 0x20, 0x00, 0x05, 3, 0, 0, 255)))
        self.assertIsNotNone(c2proxy.allowed(pkt(0x0F, 0x03, 0x30, 0, 0, 1, 2)))
        self.assertIsNotNone(c2proxy.allowed(pkt(0x0F, 0x03, 9, 0, 0, 1, 2)))

    def test_device_id_range_blocked(self):
        # GSC's SetDeviceId: 0F 03 bank 03 9E 06 + ascii
        self.assertIsNotNone(c2proxy.allowed(pkt(0x0F, 0x03, 1, 0x03, 0x9E, 6)))
        # a chunk that only overlaps the range at its end
        self.assertIsNotNone(c2proxy.allowed(pkt(0x0F, 0x03, 1, 0x03, 0x80, 58)))
        self.assertIsNone(c2proxy.allowed(pkt(0x0F, 0x03, 1, 0x03, 0x60, 58)))

    def test_switch_profile(self):
        self.assertIsNone(c2proxy.allowed(pkt(0x0F, 0x07, 1)))
        self.assertIsNone(c2proxy.allowed(pkt(0x0F, 0x07, 5)))
        self.assertIsNotNone(c2proxy.allowed(pkt(0x0F, 0x07, 0)))
        self.assertIsNotNone(c2proxy.allowed(pkt(0x0F, 0x07, 6)))

    def test_virtual_descriptor_shape(self):
        d = c2proxy.RDESC
        self.assertTrue(d.startswith(bytes.fromhex("06f0ff0940a101")))
        self.assertTrue(d.endswith(b"\xc0"))
        for rid in (b"\x85\x10", b"\x85\x12", b"\x85\x0f"):
            self.assertIn(rid, d)


if __name__ == "__main__":
    unittest.main()
