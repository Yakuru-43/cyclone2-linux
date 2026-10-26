# cyclone2

An open-source Linux tool to configure the GameSir Cyclone 2 controller:
RGB, vibration, stick and trigger deadzones, button remapping and profiles.
It replaces the Windows-only GameSir Connect app.

**Status: early reverse engineering.** Nothing here talks to the controller
yet. See [PROTOCOL.md](PROTOCOL.md) for what is known so far.

Firmware updates are out of scope. This tool will never write firmware.

## Development setup

```sh
python3 -m venv .venv
.venv/bin/pip install hid-tools
python3 -I tools/recon.py <label>   # passive: reads sysfs only, sends nothing
```

## Not affiliated with GameSir

This project is not affiliated with or endorsed by GameSir. It contains no
GameSir code, binaries or firmware.
