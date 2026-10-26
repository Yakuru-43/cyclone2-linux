import unittest
from pathlib import Path

from cyclone2.hidraw import top_level_usage_pages

RECON = Path(__file__).resolve().parent.parent / "recon"


class UsagePages(unittest.TestCase):
    def pages(self, rel):
        return top_level_usage_pages((RECON / rel).read_bytes())

    def test_cable_xinput_interface(self):
        self.assertEqual(self.pages("usb-xinput-1/rdesc-if01.bin"), [0x01, 0x0C, 0x01, 0xFFF0])

    def test_dongle_idle(self):
        self.assertEqual(self.pages("dongle-idle-1/rdesc-if00.bin"), [0x0C, 0xFF32, 0xFFF0])

    def test_dongle_xinput_same_as_cable(self):
        self.assertEqual(self.pages("dongle-xinput-1/rdesc-if01.bin"), [0x01, 0x0C, 0x01, 0xFFF0])

    def test_switch_and_ds4_have_no_config_channel(self):
        for rel in ("usb-switch-1/rdesc-if00.bin", "usb-ds4-1/rdesc-if00.bin"):
            self.assertNotIn(0xFFF0, self.pages(rel), rel)


if __name__ == "__main__":
    unittest.main()
