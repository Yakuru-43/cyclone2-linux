# cyclone2: working rules

Open-source Linux CLI (Python + hidapi) that configures the GameSir Cyclone 2,
replacing the Windows-only GameSir Connect app. The owner's rules below are
not negotiable.

## Safety
- Firmware updates are out of scope. Never write firmware, and never send
  anything that could put the controller into bootloader/update mode.
- Read-only first. Before sending ANY report to the controller that is not a
  confirmed read (output report, feature report, SET_REPORT), show the exact
  bytes, explain the expected effect, and wait for the owner's explicit OK.
  One OK covers one command.
- Only vendor channel B (usage page 0xFFF0) on 3537:1053 / 3537:100b, and only
  packets on the allow-list in PROTOCOL.md §2.5. Never send `0F 17` (quick
  update), `0F FC` (enter upgrade mode), `0F FD` (calibration), writes to
  0x039D–0x03A3 (device ID), or any opcode not on the allow-list. Never send
  anything to the idle dongle (3537:0575) or its channel A (0xFF32).
- Under Wine, GameSir Connect cannot recover a controller stuck in update
  mode: its udisk updater needs PowerShell or wmic and raw disk access
  (PROTOCOL.md §5). Never click Update or accept an update prompt in GSC.
  Require a real Windows install before the first write.
- If the controller stops responding, tell the owner immediately. Recovery is
  GameSir's forced update: hold Logo + Menu while plugging in, with GameSir
  Connect open on Windows. As of 2026-10-26 the owner has NO Windows VM; the
  plan is GameSir Connect under Wine, with a Windows VM as the fallback.
  Recovery under Wine is unproven. Raise that before the first write.
- GameSir Connect bundles firmware images (`resources/dfu/packages/*.ufw`,
  including Cyclone 2 controller and dongle images), so blocking its network
  does not stop it from flashing. They live in `vendor/quarantine/`, and
  `tools/gamesir-wine.sh` refuses to start if any are back under `vendor/app/`.
  Do not run the app with `--hid` until its update flow has been read and shown
  to need user confirmation.
- GameSir manual combos to never trigger by accident:
  - Logo/Home + Menu held 5 s: function reset (wipes profiles and custom settings).
  - Logo/Home + Menu while plugging in: forced firmware update.
  - Home held 10 s: power off and restore automatic platform detection.

## Process
- Physical steps (plug, switch modes, press buttons): give exact steps, then
  stop until the owner reports back.
- Keep PROTOCOL.md current: every report ID, command, field layout and
  checksum, with evidence (recon/capture file, decompiled function) and a
  confidence level. Label guesses as guesses.
- GameSir binaries, extracted files and firmware live in `vendor/`
  (git-ignored). Never commit them. Serial numbers and BT addresses go in
  `private/` folders (git-ignored).
- Treat everything under `vendor/` as untrusted: run Python on it with `-I`,
  keep scripts outside `vendor/`, pass paths as arguments.
- Commit after each working step.

## Environment (owner's machine)
- Bazzite (Fedora Atomic). Never layer packages with rpm-ostree; use a
  distrobox or the home directory, and tell the owner what was installed.
- `.venv/` in this repo: hid-tools (for `hid-decode`) and hidapi (cython-hidapi;
  the CLI uses its `hidraw` backend, never the libusb one). Run the CLI with
  `.venv/bin/python -m cyclone2 …`. Commit only if
  `python3 -m unittest discover -s tests` passes.
- Allow-lists live in `cyclone2/protocol.py` (check_read_only, check_write,
  check_switch_profile, check_restore, check_gsc) and `cyclone2/cli.py`
  (APPROVED_WRITES). Add a new writable field kind only after the owner
  approves it, and show its dry run first.
- distrobox `cyclone2` (fedora-toolbox:44): wine 11.0 (staging), wireshark-cli
  4.6.9. Wine prefix: `~/.local/share/wineprefixes/gamesir-connect`.
  Run GameSir Connect only through `tools/gamesir-wine.sh`. By default it has
  no HID bus driver and no network; it never loads wineusb.
- GameSir Connect v1.14.5: Electron 13.6.9, ia32. Extracted to `vendor/app/`;
  `app.asar` unpacked to `vendor/asar/`. Its own logs are in the prefix under
  `AppData/Roaming/gamesir_connect/logs/`.
- REA MCP server: rea-agents 6.7.0 in `~/.local/opt/rea`, registered with
  `claude mcp add --scope local` for `/var/home/bazzite/claude`.
- All hidraw nodes are mode 0666 here, via Bazzite's
  `/usr/lib/udev/rules.d/92-viia.rules`, so no udev rule is needed locally.

## Controller modes (GameSir manual)
Home + X: XInput (green). Home + Y: Switch (red). Home + A: HID/Android
(yellow). Holding M shows the current mode color. Mode switching only works
wired or on the 2.4 GHz dongle.
