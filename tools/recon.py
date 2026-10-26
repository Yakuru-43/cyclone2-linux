#!/usr/bin/env python3
"""Passive USB/HID recon for the GameSir Cyclone 2.

Everything here comes from sysfs, i.e. from descriptors the kernel already
read when the device enumerated. This script never opens a hidraw node and
never sends anything to the device.

Usage: python3 -I tools/recon.py LABEL [--dev 1-10]
Writes recon/LABEL/. Serial numbers go to recon/LABEL/private/ (git-ignored).
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

USB = Path("/sys/bus/usb/devices")
ROOT = Path(__file__).resolve().parent.parent
HID_DECODE = ROOT / ".venv/bin/hid-decode"
GAMESIR_VID = "3537"
HID_DIR = re.compile(r"^[0-9A-F]{4}:[0-9A-F]{4}:[0-9A-F]{4}\.[0-9A-F]{4}$")

DEVICE_ATTRS = [
    "idVendor", "idProduct", "bcdDevice", "version", "speed", "manufacturer",
    "product", "serial", "bDeviceClass", "bDeviceSubClass", "bDeviceProtocol",
    "bMaxPacketSize0", "bNumConfigurations", "bConfigurationValue",
    "bNumInterfaces", "bmAttributes", "bMaxPower", "busnum", "devnum", "devpath",
]
IFACE_ATTRS = [
    "bInterfaceNumber", "bAlternateSetting", "bInterfaceClass",
    "bInterfaceSubClass", "bInterfaceProtocol", "bNumEndpoints", "interface",
    "modalias",
]
EP_ATTRS = ["bEndpointAddress", "bmAttributes", "wMaxPacketSize", "bInterval",
            "type", "direction", "interval"]
DESC_TYPES = {1: "DEVICE", 2: "CONFIGURATION", 4: "INTERFACE", 5: "ENDPOINT",
              0x0B: "INTERFACE_ASSOCIATION", 0x21: "HID (or vendor 0x21)",
              0x22: "REPORT", 0x24: "CS_INTERFACE", 0x25: "CS_ENDPOINT"}


def read(path):
    try:
        return path.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


def find_devices():
    found = []
    for dev in sorted(USB.iterdir()):
        if ":" in dev.name or not (dev / "idVendor").exists():
            continue
        names = f"{read(dev / 'manufacturer')} {read(dev / 'product')}"
        if read(dev / "idVendor") == GAMESIR_VID or re.search(
                r"gamesir|chicken run", names, re.I):
            found.append(dev)
    return found


def walk_descriptors(raw):
    """List each standard descriptor in the cached device+config blob."""
    lines, i = [], 0
    while i + 2 <= len(raw):
        length, dtype = raw[i], raw[i + 1]
        if length < 2:
            lines.append(f"@{i:04x}: bad bLength {length}, stopping")
            break
        chunk = raw[i:i + length]
        name = DESC_TYPES.get(dtype, f"type 0x{dtype:02x}")
        lines.append(f"@{i:04x} len={length:3d} {name:22s} {chunk.hex(' ')}")
        i += length
    return lines


def hid_decode(rdesc_path):
    if not HID_DECODE.exists():
        return "(hid-decode not installed; see README)\n"
    out = subprocess.run([str(HID_DECODE), str(rdesc_path)],
                         capture_output=True, text=True)
    return out.stdout + out.stderr


def kernel_log(dev_name, hid_ids):
    try:
        out = subprocess.run(["journalctl", "-k", "-b", "--no-pager", "-o",
                              "short-iso"], capture_output=True, text=True)
    except FileNotFoundError:
        return ["(journalctl not available)"]
    keys = [f"usb {dev_name}:", f"usb {dev_name} "] + hid_ids
    return [l for l in out.stdout.splitlines() if any(k in l for k in keys)]


def redact(text, secrets):
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return text


def recon(dev, out):
    out.mkdir(parents=True, exist_ok=True)
    summary = [f"# Recon: {out.name}", "",
               f"sysfs: {dev.resolve()}", ""]
    attrs = {a: read(dev / a) for a in DEVICE_ATTRS}
    secrets = []
    if attrs["serial"]:
        secrets.append(attrs["serial"])
        (out / "private").mkdir(exist_ok=True)
        (out / "private/serial.txt").write_text(attrs["serial"] + "\n")
        attrs["serial"] = "<redacted: private/serial.txt>"

    summary.append("## Device")
    summary += [f"  {a:20s} {v}" for a, v in attrs.items() if v is not None]

    raw = (dev / "descriptors").read_bytes()
    (out / "descriptors.bin").write_bytes(raw)
    (out / "descriptors.txt").write_text("\n".join(walk_descriptors(raw)) + "\n")

    hid_ids = []
    for iface in sorted(dev.glob(f"{dev.name}:*")):
        summary += ["", f"## Interface {iface.name}"]
        for a in IFACE_ATTRS:
            v = read(iface / a)
            if v is not None:
                summary.append(f"  {a:20s} {v}")
        drv = iface / "driver"
        summary.append(f"  {'driver':20s} "
                       f"{drv.resolve().name if drv.exists() else '(none)'}")
        for ep in sorted(iface.glob("ep_*")):
            vals = " ".join(f"{a}={read(ep / a)}" for a in EP_ATTRS)
            summary.append(f"  {ep.name}: {vals}")
        for usbmisc in sorted(iface.glob("usbmisc/*")):
            summary.append(f"  usbmisc: {usbmisc.name}")

        for hid in sorted(p for p in iface.iterdir() if HID_DIR.match(p.name)):
            hid_ids.append(hid.name)
            tag = f"if{read(iface / 'bInterfaceNumber')}"
            summary += ["", f"### HID {hid.name} ({tag})"]
            uevent = read(hid / "uevent") or ""
            for line in uevent.splitlines():
                key, _, val = line.partition("=")
                if key == "HID_UNIQ" and val:
                    secrets.append(val)
                summary.append(f"  {line}")
            nodes = [p.name for p in (hid / "hidraw").glob("hidraw*")]
            summary.append(f"  hidraw: {', '.join(nodes) or '(none)'}")
            for inp in sorted((hid / "input").glob("input*")):
                handlers = [p.name for p in inp.iterdir()
                            if re.match(r"(event|js)\d+$", p.name)]
                summary.append(f"  {inp.name}: name={read(inp / 'name')!r} "
                               f"handlers={','.join(handlers)}")
            rdesc = (hid / "report_descriptor").read_bytes()
            (out / f"rdesc-{tag}.bin").write_bytes(rdesc)
            (out / f"rdesc-{tag}.txt").write_text(
                hid_decode(hid / "report_descriptor"))
            summary.append(f"  report descriptor: {len(rdesc)} bytes -> "
                           f"rdesc-{tag}.bin / rdesc-{tag}.txt")

    log = kernel_log(dev.name, hid_ids)
    (out / "kernel.log").write_text(redact("\n".join(log), secrets) + "\n")
    text = redact("\n".join(summary), secrets) + "\n"
    (out / "summary.txt").write_text(text)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("label", help="e.g. usb-xinput, dongle-xinput")
    ap.add_argument("--dev", help="sysfs USB device name, e.g. 1-10")
    args = ap.parse_args()

    if args.dev:
        devs = [USB / args.dev]
    else:
        devs = find_devices()
    if len(devs) != 1:
        print(f"expected exactly one GameSir device, found "
              f"{[d.name for d in devs]}; pass --dev", file=sys.stderr)
        subprocess.run(["lsusb"])
        return 1

    out = ROOT / "recon" / args.label
    if out.exists():
        shutil.rmtree(out)
    print(recon(devs[0], out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
