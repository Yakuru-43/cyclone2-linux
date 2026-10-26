# cyclone2

Configure the **GameSir Cyclone 2** controller from Linux: RGB lighting,
stick and trigger deadzones, hair trigger, button remapping, profiles and
vibration strength. It replaces the Windows-only GameSir Connect app for
these settings.

**Firmware updates are out of scope.** This tool never writes firmware, and
it refuses the commands that start an update, as well as calibration
(see [Safety](#safety)).

Not affiliated with or endorsed by GameSir. Contains no GameSir code,
binaries or firmware.

## Status

Tested on a Cyclone 2 with controller firmware V3.4.6 and dongle V1.2.0, on
Bazzite (Fedora 44, kernel 7.2), over the USB cable in XInput mode.

| Setting | Command | Verified |
|---|---|---|
| Info: firmware versions, active profile | `cyclone2 info` | on device |
| Read / back up / restore all settings | `get`, `backup`, `restore` | on device |
| RGB brightness and colours | `set brightness`, `set color` | on device, visible |
| Stick and trigger deadzones | `set deadzone` | on device, by feel |
| Hair trigger (off / adaptive) | `set hair-trigger` | on device, by feel |
| Button remapping (to gamepad buttons) | `set map` | on device, by feel |
| Switch the active profile | `profile` | on device |
| Vibration strength | `set vibration` | on device, by feel |

The 2.4 GHz dongle exposes the same configuration channel (3537:100b) and
should work, but it has not yet been tested with this tool. Bluetooth is not
supported.

## Install (Bazzite / Fedora Atomic)

No system packages and no `rpm-ostree` needed:

```sh
git clone <this repository> cyclone2 && cd cyclone2
pip install --user .          # installs `cyclone2` into ~/.local/bin, plus hidapi
```

The controller must be in **XInput mode** (the Home LED shows green when you
hold M). Mode switching: hold View + Menu to cycle XInput → Switch → DS4.

**Permissions.** `cyclone2` talks to the controller's hidraw node. Bazzite
already lets every user open hidraw devices. On other distributions,
install the udev rule:

```sh
sudo cp udev/60-cyclone2.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Usage

```sh
cyclone2 info                          # firmware versions, active profile
cyclone2 backup ~/cyclone2-backup.json # save every setting (do this first)
cyclone2 get --bank all --json         # decode settings

cyclone2 set brightness 60             # RGB brightness of the active effect, 0-100
cyclone2 set color right=#00ff00       # zones: left, right, m (dot above M), home, zone2
cyclone2 set deadzone left=12          # stick inner deadzone in %; left=12:95 sets outer too
cyclone2 set deadzone lt=8             # trigger deadzone (lt, rt)
cyclone2 set hair-trigger rt=adaptive  # off | adaptive
cyclone2 set map A=B                   # A outputs B; `set map A=A` resets it
cyclone2 set vibration 4               # levels 0-4; left=N / right=N for one motor
cyclone2 profile 2                     # switch the active profile (1-4)

cyclone2 restore ~/cyclone2-backup.json --plan   # show what would change
cyclone2 restore ~/cyclone2-backup.json          # write back only the differences
```

`set` acts on the active profile unless you pass `--profile 1-4|shift`.
Lighting is shared by all profiles. Add `--dry-run` to any command to print
the exact packets without sending anything. `cyclone2 rumble` plays a
1-second test rumble (normal game rumble, via the kernel), which is handy
for checking vibration settings.

## Safety

- Every outgoing packet is checked against an **allow-list**: reads, and
  writes that land exactly on a supported setting with a valid value.
  Everything else is refused before it reaches the controller. That includes
  the update commands (`0F 17`, `0F FC`), calibration (`0F FD`), device-ID
  writes, and any opcode we don't know.
- Every write is read back and compared.
- Only the bytes of the setting you change are written. GameSir Connect
  re-encodes whole profiles; this tool does not.
- If something ever goes wrong, GameSir's own recovery (forced update from
  GameSir Connect on Windows: hold Logo + Menu while plugging in) is still
  available. It needs real Windows; it does not work under Wine.

## How it works

The controller's HID interface carries a vendor channel (usage page 0xFFF0):
commands go out as report 0x0F and replies come back as report 0x10.
Settings live in "banks": profiles 1–4 and a Shift layer (680 bytes each),
plus a shared lighting bank (635 bytes). They are read and written in
58-byte chunks with no checksum. [PROTOCOL.md](PROTOCOL.md) documents every
command, field and confidence level, with the evidence behind each.

## Development

- `tools/recon.py`: passive USB/HID recon from sysfs.
- `tools/c2proxy.py` + `tools/gamesir-wine.sh`: run GameSir Connect under
  Wine (in a distrobox) against a virtual copy of the controller. Every
  packet is logged, and the update commands are filtered. Needs
  `sudo setfacl -m u:$USER:rw /dev/uhid`.
- `tools/capture_writes.py`, `tools/banks_from_capture.py`: analyse captures.
- Tests: `python3 -m unittest discover -s tests`.

## Credits

- [InFract](https://github.com/NaokoAF/InFract) by Nicole Rivers (MIT),
  whose Cyclone 2 notes were the starting point for the protocol work.
- MIT licensed; see [LICENSE](LICENSE).
