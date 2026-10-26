# GameSir Cyclone 2 protocol notes

Everything known about how the controller talks to GameSir Connect, with the
evidence behind each claim.

**Confidence levels**
- **confirmed**: observed on the device, and matches code or a capture.
- **high**: one strong source (a descriptor, decompiled code, or a capture).
- **medium**: inferred from indirect evidence.
- **guess**: a hypothesis to test. Never act on it without checking first.

## 1. Device identification and transport

### 1.1 USB identities per connection and mode

| Connection | Mode | VID:PID | bcdDevice | Interfaces | Evidence | Confidence |
|---|---|---|---|---|---|---|
| unknown (cable or dongle, TBD) | unknown | 3537:0575 | 1.20 | 1 × HID (03/00/00) | `recon/as-found/` | IDs confirmed; connection and mode unknown |

VID 0x3537 is "Guangzhou Chicken Run Network Technology Co., Ltd." (GameSir).
The kernel reports `SerialNumber=0` (no serial string) for 3537:0575.

### 1.2 3537:0575: single HID interface

Evidence: `recon/as-found/descriptors.txt`, `recon/as-found/rdesc-if00.txt`.
Confidence: **confirmed** (read from the descriptors the kernel cached).

- Full speed, one configuration, bus-powered, 500 mA.
- Interface 0: HID class, no boot protocol, bound to `usbhid`/`hid-generic`.
- EP 0x82 interrupt IN, 64 bytes, 1 ms. EP 0x02 interrupt OUT, 64 bytes, 1 ms.
  Output reports therefore travel on the interrupt OUT endpoint, not control
  SET_REPORT.
- Report descriptor: 114 bytes, HID 1.11. **No feature reports.**
- **No Generic Desktop gamepad or joystick collection.** Stick and button data
  is not described in this descriptor. Linux creates two input devices: a
  Consumer Control one (Vol+/Vol−), and one with only ABS_MISC, which comes
  from the vendor collection.

| Collection | Usage page / usage | Report ID | Direction | Payload (bytes, excluding ID) | Item usage |
|---|---|---|---|---|---|
| Consumer Control | 0x000C / 0x0001 | 0xDD | In | 1 (bit 0 Vol+, bit 1 Vol−, 6 padding bits) | 0xE9, 0xEA |
| Vendor A | 0xFF32 / 0x74 | 0x41 | In | 63 | 0xFF32:0x75 |
| Vendor A | 0xFF32 / 0x74 | 0x41 | Out | 63 | 0xFF32:0x76 |
| Vendor B | 0xFFF0 / 0x40 | 0x10 | In | 63 | 0xFF00:0x00 |
| Vendor B | 0xFFF0 / 0x40 | 0x12 | In | 63 | 0xFF00:0x21 |
| Vendor B | 0xFFF0 / 0x40 | 0x0F | Out | 63 | 0xFF00:0x22 |

Every vendor report is 64 bytes on the wire (ID + 63), which equals the
endpoint size.

### 1.3 Open questions
- Which vendor channel (A: 0x41, or B: 0x0F/0x10/0x12) carries configuration,
  and which carries firmware update? **Unknown.** Nothing is to be sent on
  either until GameSir Connect's code maps them (step 3).
- What is plugged in at `as-found`: the cable or the dongle? In which mode?
- Where does gamepad input go in this mode, given there is no gamepad
  collection? *Guess:* this is not a gamepad mode, for example a dongle with
  no controller linked, or a charge/config-only state.

## 2. Commands

(none yet)

## 3. Checksums / CRC

(none yet)

## 4. Settings → bytes

(none yet)

## 5. Firmware-update paths (must never be triggered)

This section lists what to avoid. None of it is to be implemented.

Evidence: GameSir Connect v1.14.5 installer (`GameSir Connect Setup.exe`,
NSIS 3.03 → `$PLUGINSDIR/app-32.7z`), Electron 13.6.9 ia32, extracted locally
(not committed).

- `resources/dfu/` holds the update code and tools: JieLi
  (`jl_firmware_upgrade_x86.dll`, `JL_upgradeUtility_x86.dll`), the "SAPJoyUni"
  SDK used by T3P/T4N2 controllers (`sapjoyuniapis_*.dll`), a USB
  mass-storage ("udisk") updater (`uDiskDfuUtil.ts`, `udiskUpgradWoker.js`),
  and worker scripts (`upgradWoker.js`, `t3proUpdateWorker.js`). Confidence
  **high** that these exist; their exact roles are **medium**, inferred from
  their names.
- `resources/dfu/packages/` bundles 48 firmware images (`.ufw`). Ten are for
  the Cyclone 2: `GS_C2_DEVICE_V123_{Key,No_Key}`,
  `GS_C2_ADC_DEVICE_V{346,352}_{Key,No_Key}`,
  `GS_C2_Dongle_V{119,121}_{Key,No_Key}`. So the app can flash with no network.
  **high**
- The app polls for disks at startup (`getAllOfDisks` via PowerShell in its
  log). *Guess:* this looks for a device in mass-storage update mode.
- `main.js`: an IPC event `Update_KZ {deviceId, packagePath}` posts
  `{action: "startDfu"}` to the T3P update worker. **high** (read in the code);
  what triggers it is not yet traced.
- To do (step 3): find what triggers each update path, whether it starts
  without user confirmation, and which report or command enters update or
  bootloader mode. Those bytes go on a deny-list in the CLI.
