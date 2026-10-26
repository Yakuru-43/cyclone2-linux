"""Play a rumble effect through the controller's XInput (xpad) event device.

This is ordinary game rumble: the kernel's force-feedback API, which xpad
turns into a standard XInput rumble report. It does not use the vendor
configuration channel.
"""

import fcntl
import os
import struct
import time
from pathlib import Path

EV_FF, FF_RUMBLE = 0x15, 0x50
FF_EFFECT_SIZE = 48                      # struct ff_effect on 64-bit Linux
EVIOCSFF = (1 << 30) | (FF_EFFECT_SIZE << 16) | (ord("E") << 8) | 0x80
EVIOCRMFF = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x81


def find_event_node(hidraw_path):
    """The xpad event device on the same USB device as the config channel."""
    hid = (Path("/sys/class/hidraw") / Path(hidraw_path).name / "device").resolve()
    usb_device = hid.parent.parent
    for event in sorted(usb_device.glob("*:1.0/input/input*/event*")):
        return f"/dev/input/{event.name}"
    raise FileNotFoundError("no XInput event device found for this controller")


def rumble(event_path, strong, weak, ms):
    """strong/weak in percent, duration in milliseconds."""
    effect = bytearray(struct.pack("<HhHHHHH", FF_RUMBLE, -1, 0, 0, 0, ms, 0))
    effect += b"\0\0" + struct.pack("<HH", strong * 0xFFFF // 100, weak * 0xFFFF // 100)
    effect = effect.ljust(FF_EFFECT_SIZE, b"\0")
    fd = os.open(event_path, os.O_RDWR)
    try:
        fcntl.ioctl(fd, EVIOCSFF, effect)              # kernel fills in the effect id
        effect_id = struct.unpack_from("<h", effect, 2)[0]
        os.write(fd, struct.pack("<qqHHi", 0, 0, EV_FF, effect_id, 1))
        time.sleep(ms / 1000 + 0.1)
        fcntl.ioctl(fd, EVIOCRMFF, effect_id)
    finally:
        os.close(fd)
