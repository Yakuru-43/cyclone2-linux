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
| 2.4 GHz dongle, controller off | (none linked) | 3537:0575 | 1.20 | 1 × HID (03/00/00) | `recon/dongle-idle-{1,2}/` (identical) | **confirmed**: the device disappeared when only the dongle was unplugged |
| USB cable, controller off | n/a | (does not enumerate) | | | kernel log 2026-10-26 19:24 | **confirmed** |
| USB cable | XInput (green LED) | 3537:1053 | 3.46 | if0 XInput (ff/5d/01, `xpad`) + if1 HID | `recon/usb-xinput-{1,2,3}/` (identical) | **confirmed** |
| USB cable | Switch | 057e:2009 | 3.46 | 1 × HID (Pro Controller layout) | `recon/usb-switch-1/` | **confirmed** |
| USB cable | DS4 (blue LED) | 054c:09cc | 3.46 | 1 × HID (DualShock 4 layout) | `recon/usb-ds4-{1,2}/` (identical) | **confirmed** |
| 2.4 GHz dongle, controller linked | XInput (green) | 3537:100b | 1.20 | if0 XInput (`xpad`) + if1 HID | `recon/dongle-xinput-{1,2}/` (identical) | **confirmed** |
| 2.4 GHz dongle, controller linked | Switch (red) | 057e:2009 | 1.20 | 1 × HID | `recon/dongle-switch-1/` | **confirmed** |
| 2.4 GHz dongle, controller linked | DS4 (blue) | 054c:09cc | 1.20 | 1 × HID | `recon/dongle-ds4-1/` | **confirmed** |

VID 0x3537 is "Guangzhou Chicken Run Network Technology Co., Ltd." (GameSir).
In Switch and DS4 modes the controller borrows Nintendo's and Sony's IDs.

- **The dongle changes identity.** On its own it is 3537:0575, with vendor
  channels A and B. Once the controller links (about 7 s after power-on), it
  re-enumerates as whatever mode the controller is in. In each mode its
  report descriptors are byte-identical to the cable's: dongle Switch ==
  cable Switch, dongle DS4 == cable DS4, and dongle XInput if1 == cable
  XInput if1. Channel A only exists while no controller is linked. Only the
  product strings differ: "Pro Controller" on the dongle against "Gamepad" on
  the cable in Switch mode. bcdDevice stays the dongle's 1.20 throughout.
- **Serial number.** Only the XInput identities have a serial string. The
  dongle in XInput mode reports the **same** serial as the controller over
  the cable, so the serial identifies the controller (or the pair), not the
  transport. The value is kept in git-ignored `private/`.
- **LEDs.** Holding M shows the mode colour: XInput green, Switch red, DS4
  blue, the same on cable and dongle. While linked to the dongle the Home LED
  is solid white.
- **For the CLI and udev rule:** configuration targets are 3537:1053 (cable)
  and 3537:100b (dongle), both XInput, HID interface 1. 3537:0575 is the idle
  dongle.

**Firmware versions.** bcdDevice is the firmware version: 3.46 = controller
V3.4.6, 1.20 = dongle V1.2.0. GameSir's FAQ quotes versions in this form
("V1.1.9 or above for dongle, V3.4.4 or above for controller"), and the
bundled images are named `GS_C2_ADC_DEVICE_V346`/`V352` and
`GS_C2_Dongle_V119`/`V121` (§5). Confidence **medium**. It follows that
GameSir Connect has newer images than both devices are running.

**Mode switching (firmware V3.4.6, cable and dongle), observed 2026-10-26.** Holding
View + Menu cycles XInput → Switch → DS4 → XInput, and each step
re-enumerates the device. Home + X switched XInput → DS4 (blue). Home + Y
flashed the LED but changed nothing; Home + A did nothing. This differs from
GameSir's online manual. Power-on came up in XInput. Confidence **confirmed**
(USB enumerations recorded by `tools/recon.py --watch`).

### 1.2 3537:0575 (dongle): single HID interface

Evidence: `recon/dongle-idle-1/descriptors.txt`, `recon/dongle-idle-1/rdesc-if00.txt`.
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

### 1.3 3537:1053 (cable, XInput): interface 1 (HID)

Evidence: `recon/usb-xinput-1/rdesc-if01.txt` (214 bytes).
Confidence: **confirmed**.

- Interface 0 is a standard XInput gamepad (EP 0x82 in / 0x02 out, 32 bytes),
  bound to `xpad`. Gamepad data goes there, not over HID.
- Interface 1: EP 0x84 interrupt IN, 64 bytes, 1 ms; EP 0x04 interrupt OUT,
  64 bytes, 5 ms. No feature reports.

| Collection | Usage page / usage | Report ID | Direction | Payload (bytes) |
|---|---|---|---|---|
| Keyboard | 0x0001 / 0x06 | 0x03 | In 8 (modifiers, reserved, 6 keys); Out 1 (3 LED bits) | |
| Consumer | 0x000C | 0x02 | In | 4 (2 × 16-bit usages) |
| Mouse | 0x0001 / 0x02 | 0x09 | In | 5 buttons, X/Y 16-bit rel, wheel 8-bit rel |
| Vendor B | 0xFFF0 / 0x40 | 0x10 | In | 63 |
| Vendor B | 0xFFF0 / 0x40 | 0x12 | In | 63 |
| Vendor B | 0xFFF0 / 0x40 | 0x0F | Out | 63 |

Vendor channel B is byte-for-byte the same collection as on the dongle (§1.2).
*Guess:* the keyboard and mouse collections let button remaps emit
keystrokes and mouse input.

### 1.4 Which channel is configuration
- Vendor channel **B** (0xFFF0: 0x0F out, 0x10/0x12 in) is present on the
  controller in XInput mode and on the dongle. It is absent in Switch and DS4
  modes. GameSir's FAQ says to use GameSir Connect "via dongle or Cable in
  Xinput mode". *Guess* (medium): B is the GameSir Connect configuration
  channel.
- Vendor channel **A** (0xFF32: 0x41 in/out) appears only on the dongle.
  *Guess:* it belongs to the dongle itself (pairing, its own settings or its
  own update).
- Firmware update may share these channels. Nothing is to be sent on either
  until GameSir Connect's code maps the commands (step 3).

### 1.5 Open questions
- Does the dongle relay configuration to the controller over the air, or
  does it hold some settings itself? To answer from code (step 3) and captures
  (step 4).

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
