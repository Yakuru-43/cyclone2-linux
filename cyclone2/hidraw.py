"""Minimal Linux hidraw access using only the standard library."""

import os
import select
from pathlib import Path

SYSFS = Path("/sys/class/hidraw")


def top_level_usage_pages(rdesc):
    """Usage page of every top-level collection in a HID report descriptor."""
    pages, page, depth, stack, i = [], 0, 0, [], 0
    while i < len(rdesc):
        prefix = rdesc[i]
        if prefix == 0xFE:  # long item
            i += 3 + (rdesc[i + 1] if i + 1 < len(rdesc) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(rdesc[i + 1:i + 1 + size], "little")
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1 and tag == 0x0:      # Usage Page
            page = value
        elif kind == 1 and tag == 0xA:    # Push
            stack.append(page)
        elif kind == 1 and tag == 0xB and stack:  # Pop
            page = stack.pop()
        elif kind == 0 and tag == 0xA:    # Collection
            if depth == 0:
                pages.append(page)
            depth += 1
        elif kind == 0 and tag == 0xC:    # End Collection
            depth = max(0, depth - 1)
        i += 1 + size
    return pages


def devices():
    """Every hidraw node: path, vid, pid, name, top-level usage pages, virtual."""
    found = []
    if not SYSFS.is_dir():
        return found
    for node in sorted(SYSFS.iterdir(), key=lambda p: int(p.name[6:] or 0)):
        dev = node / "device"
        try:
            uevent = dict(line.split("=", 1) for line in
                          (dev / "uevent").read_text().splitlines() if "=" in line)
            _bus, vid, pid = (int(x, 16) for x in uevent["HID_ID"].split(":"))
            pages = top_level_usage_pages((dev / "report_descriptor").read_bytes())
        except (OSError, KeyError, ValueError):
            continue
        found.append({"path": f"/dev/{node.name}", "vid": vid, "pid": pid,
                      "name": uevent.get("HID_NAME", ""), "usage_pages": pages,
                      "virtual": "/virtual/" in str(dev.resolve())})
    return found


class Device:
    """An open hidraw node: write whole reports, read with a timeout."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def write(self, data):
        return os.write(self.fd, data)

    def read(self, size, timeout_ms):
        ready, _, _ = select.select([self.fd], [], [], timeout_ms / 1000)
        if not ready:
            return b""
        try:
            return os.read(self.fd, size)
        except BlockingIOError:
            return b""

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
