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
  Xinput mode". InFract's driver opens exactly this collection (VID 0x3537,
  PID 0x1053 or 0x100b, usage page 0xFFF0) and its notes say GSC configures
  the device through it (§2). Confidence **high** that B is the configuration
  channel: two independent sources agree. It becomes confirmed when we see
  a reply on the device.
- Vendor channel **A** (0xFF32: 0x41 in/out) appears only on the dongle.
  *Guess:* it belongs to the dongle itself (pairing, its own settings or its
  own update).
- Firmware update may share these channels. Nothing is to be sent on either
  until GameSir Connect's code maps the commands (step 3).

### 1.5 Open questions
- Does the dongle relay configuration to the controller over the air, or
  does it hold some settings itself? To answer from code (step 3) and captures
  (step 4).

## 2. Commands (vendor channel B)

**Source for this section:** InFract, MIT, by Nicole Rivers
(github.com/NaokoAF/InFract, commit 6d54c5d, 2026-07-01). Files:
`Drivers/GameSir/Cyclone2Notes.md`, `Drivers/GameSir/Cyclone2Driver.cs`, and
`Gamepads/GameSir/Cyclone2/*.cs`. This is third-party reverse engineering
that we have **not yet** checked against GameSir Connect's code (step 3) or
captures (step 4). Everything here is **medium** unless marked otherwise.
InFract's own author flags parts of it as uncertain.

### 2.1 Framing
- Host → device: output report **0x0F**. Byte 0 after the report ID is the
  command, followed by its arguments, padded to 63 bytes (64 on the wire).
  InFract writes Heartbeat as just 2 bytes (`0F F2`) and Rumble as a full,
  zero-padded 64-byte report.
- Device → host: input report **0x10**, carrying replies. Byte 0 after the ID
  is the reply code.
- Device → host: input report **0x12**, an input-state stream. It only flows
  while heartbeats keep arriving (§2.3).
- No checksum or CRC appears in InFract's packets. (Step 3 checks whether GSC
  adds one.)

### 2.2 Command table

"Kind" says what a command does to the device: **read** (no state change),
**volatile** (changes run-time state only, gone on power-off), or **write**
(changes stored settings or the active profile).

| Dir | Code | Name | Arguments / payload | Reply | Kind |
|---|---|---|---|---|---|
| out | 0xF2 | Heartbeat | none | 0x12 stream starts | volatile |
| out | 0x09 | Firmware info | none | 0x0A | read |
| in | 0x0A | Firmware info reply | 16 bytes "unicode": first 8 = controller version, last 8 = dongle version | | |
| out | 0x0B | Get profile | none | 0x0C | read |
| in | 0x0C | Get profile reply | `XX` = current profile (1–4) | | |
| out | 0x04 | Read register | `bank, addr_hi, addr_lo, len` (len ≤ 58) | 0x05 | read |
| in | 0x05 | Read register reply | `bank, addr_hi, addr_lo, len, data…` | | |
| out | 0x10 | Read register (alt) | same as 0x04 | 0x11 | read. *Guess (InFract):* reads live memory rather than flash. GSC uses it after 0x0F. |
| in | 0x11 | Read register (alt) reply | header + data | | |
| out | 0x03 | Write register | `bank, addr_hi, addr_lo, len, data…` | 0x06 | **write** |
| out | 0x07 | Set profile | `XX` (1–4) | 0x06 | **write** |
| in | 0x06 | Ack | none (reply to 0x03 and 0x07) | | |
| out | 0x06 | "Registers loaded" (?) | none; GSC sends it at startup after reading all registers | ? | **unknown**, treat as write |
| out | 0x0D | Select RGB keyframe | `01 XX` | ? | **unknown**, treat as write |
| out | 0x20 | Rumble | `66 55 L R` (`66 55` unexplained) | none | volatile |
| in | 0x0F | Register changed | `bank, addr_hi, addr_lo, len_hi?, len_lo`, no data; sent when the user changes a setting with the M button | | |

Notes:
- **Heartbeat.** GSC sends it once a second, InFract every 500 ms. On the
  dongle the 0x12 stream is limited to 200 Hz.
- **What InFract actually sends:** only Heartbeat and Rumble, never a
  register write. Those two are proven harmless only to the extent that
  InFract users run them.

### 2.3 Input stream (report 0x12), 63 bytes

Uses the DS4 layout, plus "raw" values (before remapping and deadzones) in
bytes 53–61. Offsets exclude the report ID.

| Offset | Field |
|---|---|
| 0–3 | LX, LY, RX, RY (u8) |
| 4–5 | buttons: bits 0–3 D-pad (0=N, clockwise to 7=NW, other = released), 4 X, 5 A, 6 B, 7 Y, 8 LB, 9 RB, 10 LT, 11 RT, 12 View, 13 Menu, 14 LS, 15 RS |
| 6 | special: bit 0 Home, 1 Share, 3 L4, 4 R4, 5 M. L4, R4 and M only show in the raw copy (byte 59). |
| 7–8 | LT, RT (u8) |
| 9–10 | timestamp, u16 LE, units of 5.33 µs |
| 12–23 | gyro X/Y/Z (±2000 °/s), accel X/Y/Z (±4 g), i16 LE |
| 34–41 | touchpad slots (DS4 format) |
| 53–61 | raw LX, LY, RX, RY, raw buttons (57–58), raw special (59), raw LT, RT |

### 2.4 Registers

Address = (bank, 16-bit big-endian address). Values from InFract's
`Cyclone2Registry.cs`.

| Bank | Address | Meaning | Values |
|---|---|---|---|
| 1–4 (per profile) | 0x0020 | left rumble strength | 0–100 |
| 1–4 | 0x0021 | right rumble strength | 0–100 |
| 1–4 | 0x002E | polling rate | 0 = 250 Hz, 1 = 500 Hz, 2 = 1000 Hz |
| 0x20 (RGB) | 0x0000 | active RGB profile | ? |
| 0x20 | 0x0001 | RGB profile 1: keyframe count | 1–8 |
| 0x20 | 0x0003 | RGB 1: speed | 0–20 (higher = slower) |
| 0x20 | 0x0004 | RGB 1: brightness | 0–100 |
| 0x20 | 0x0005 / 0x0008 / 0x000B / 0x000E / 0x0011 | RGB 1 colours: left / right / unknown / Home / dots | 3 bytes each (*guess:* R, G, B) |
| 0x20 | 0x007D / 0x00F9 / 0x0175 | RGB profiles 2 / 3 / 4: keyframe count | so each RGB profile is 0x7C (124) bytes apart |
| 0x30 | 0x0000 | selected profile | 1–4 |

### 2.5 Send policy for our tool

The CLI talks only to vendor channel B, on 3537:1053 or 3537:100b, and checks
every outgoing command against an **allow-list**. There is no deny-list:
anything not listed is refused.
- Allowed from step 5 on, once the owner approves the first packet: 0x09,
  0x0B, 0x04 (reads), and 0xF2 (volatile, needed for the input stream).
- Allowed only after per-command approval in step 6: 0x03, 0x07, and whatever
  step 3 finds RGB needs.
- Never: any code that step 3 links to firmware update, bootloader or DFU,
  whatever its name. Nothing ever goes on the idle dongle (3537:0575) or its
  channel A.

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
