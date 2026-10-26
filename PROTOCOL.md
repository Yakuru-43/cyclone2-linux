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

**Sources.** The primary source is GameSir Connect v1.14.5,
`dist/electron/main.js` inside `app.asar`. Line numbers ("l.") refer to
`vendor/pretty/main.js`, our Prettier 3.3.3 rendering at
`--print-width 120` (local only, not committed). Key places:
- command builder `Ro`: l. 14883
- reply decoder `wo`/`xo`: l. 15244–15355
- constants `Ya.Values`: l. 14277
- Cyclone 2 class `ys`: l. 15547–16044
- profile codecs `$a`: l. 14436, and `qo`: l. 15399, with helpers at l. 8382–8960
- device catalogue: l. 36650–36735

The cross-check is InFract @6d54c5d (MIT, by Nicole Rivers). Where GSC's
code and InFract agree, the confidence is **high**. Nothing is **confirmed**
until it has been seen on the device (steps 4–5).

### 2.1 Framing
- **Host → device:** output report **0x0F**, with the opcode in byte 1. GSC
  builds short arrays (for example `[15, 9]`), and node-hid on Windows sends
  the full 64-byte report, zero-padded. **Our tool always sends 64 bytes,
  zero-padded.** **high**
- **Device → host:** report **0x10** carries replies, opcode in byte 1 (§2.3).
  Report **0x12** is the input stream (§2.4).
- **Lockstep.** GSC's queue (`writeWithAck`) sends one command, waits for any
  recognised reply, then sends the next. **high**
- **No checksum or CRC** in the framing or in the profile data (§3). **high**

### 2.2 Host → device opcodes (byte 1 of report 0x0F)

Kinds: **read** = no state change; **volatile** = run-time state only;
**write** = changes stored settings or the active profile.

| Op | GSC builder (`Ro.*`) | Bytes after `0F` | Kind | Reply | Notes |
|---|---|---|---|---|---|
| 0x04 | getReadProfileCommand | `04 bank offH offL len` (len ≤ 58) | read | 05 | GSC reads whole banks in 58-byte chunks |
| 0x09 | getReadFWVersionCommand | `09` | read | 0A | |
| 0x0B | getReadCurrentProfileCommand | `0B` | read | 0C | |
| 0x10 | getRefreshProfileCommandByOffset | `10 bank offH offL len` | read | 11 | used after a 0F "ProfileChanged" |
| 0x0D | getReadRGBCommand | `0D` | read | 0E | |
| 0xF2 | getHeartBeatCommand | `F2 00` | volatile | — | keeps the 0x12 stream flowing |
| 0x20 | getVibrationCommand | `20 66 55 L R` | volatile | — | GSC sends `L=R=0` after the test duration |
| 0x0D | getSetLightStatusCommand | `0D play frame` | volatile? | ? | LED preview (play/pause, frame) |
| 0x16 | getSetMacroStatusCommand | `16 key state` (key 1 = L4, 2 = R4) | volatile? | ? | macro recording |
| 0x03 | getWriteProfileCommand | `03 bank offH offL len data…` (≤ 58 data bytes) | **write** | 06 | GSC writes only the changed byte range |
| 0x07 | getSwitchProfileCommand | `07 n` (1–4; Shift = 5) | **write** | 06 | |
| 0x01 / 0x02 | Enter/ExitProfileConfig | `01` / `02 b` | ? | | not used by the Cyclone 2 class |

**Forbidden.** Our tool must never send these:

| Op | GSC builder | Bytes after `0F` | Why |
|---|---|---|---|
| **0x17** | getQuickUpdateCommand | `17 55 88` | Sent when the user confirms a firmware update (`reqQuickUpdate`); GSC then sets `isManualUpdating`. *Guess (medium):* reboots the controller into update mode (§5). |
| **0xFC** | getEnterUpgradeModeCommand | `FC` | Enters upgrade mode. The reply decoder calls opcode 0xFC "RequestToUpgrade". |
| **0xFD** | getSetCalibrationStateCommand | `FD n` | Stick/trigger calibration state. |
| 0x03 → 0x039E | getSetDeviceIdCommand | `03 bank 03 9E 06` + 6 ASCII bytes | Overwrites the device ID. |
| 0x08, 0x12, 0x14, 0xF0 | (decoder names only) | | WriteProfileToEEPRom, WriteEEPRom, ReadEEPRom, Download. No builder exists in GSC. |

### 2.3 Device → host replies (byte 1 of report 0x10)

Decoder `wo`, l. 15277:

| Op | Name | Layout / handling in GSC |
|---|---|---|
| 0x05 | ReadProfileAck | `10 05 bank offH offL len data…`, with data from byte 6. Once `off + len` equals the bank size (680, or 635 for bank 0x20), GSC treats it as "ReadProfileComplete". |
| 0x06 | Ack | `10 06 01` means "AckWithBusy" (GSC logs it and keeps waiting). |
| 0x0A | ReadFirmwareVersionAck | `resolveFwVersion`: controller = bytes 4–8 and dongle = bytes 12–16, as text. Spaces and NULs are stripped and the characters joined with "." (so "346" → "3.4.6"). InFract describes "16 bytes unicode". *To observe.* |
| 0x0C | ReadCurrentProfileAck | `10 0C n`. GSC treats 0 as 1. If n > 4 or n is Shift, **GSC itself switches to profile 1** (a write); our tool must not copy that. |
| 0x0E | ReadRGBAck | |
| 0x0F | ProfileChanged | `10 0F bank offH offL lenH lenL`. The user changed something on the controller; GSC re-reads that range with 0x10. Bank 0x30 means the profile number changed. |
| 0x11 | RefreshProfileAck | same layout as 0x05 |
| 0x01–0x04, 0x07–0x09, 0x0B, 0x0D, 0x10 | echoes of the request opcodes | |
| 0x08, 0x12–0x15, 0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xFC | EEPROM, Download, HeartBeat, KeyStatus, RequestToUpgrade | names only |

### 2.4 Input stream (report 0x12)
- GSC treats any report with byte 0 = 18 as gamepad state. Offsets below
  **include** the report ID at byte 0.
- `parseGamepadState` (l. ~15770) reads byte 35 = charge state, 36 = battery
  level, and 37 = active light config index. Bytes 38–52 are the 5 live LED
  colours (R, G, B) in the order Home, LowerLeft, LowerRight, UpperLeft,
  UpperRight. Byte 53, bits 0 and 1: macro recording; bits 4 and 5: which
  back button. **high**
- **Conflict:** InFract puts DS4 touchpad data at bytes 34–41 (excluding the
  ID, so 35–42 including it). That overlaps what GSC parses as
  battery/LED. GSC's code wins; resolve by observation.
- InFract's layout, offsets **excluding** the ID (add 1 for report offsets),
  is **medium**:

| Offset | Field |
|---|---|
| 0–3 | LX, LY, RX, RY (u8) |
| 4–5 | buttons: bits 0–3 D-pad (0 = N, clockwise to 7 = NW, other = released), 4 X, 5 A, 6 B, 7 Y, 8 LB, 9 RB, 10 LT, 11 RT, 12 View, 13 Menu, 14 LS, 15 RS |
| 6 | special: bit 0 Home, 1 Share, 3 L4, 4 R4, 5 M (L4, R4 and M only in the raw copy at 59) |
| 7–8 | LT, RT (u8) |
| 9–10 | timestamp, u16 LE, units of 5.33 µs |
| 12–23 | gyro X/Y/Z (±2000 °/s), accel X/Y/Z (±4 g), i16 LE |
| 53–61 | raw LX, LY, RX, RY, raw buttons (57–58), raw special (59), raw LT, RT |

### 2.5 Send policy for our tool

The CLI talks only to vendor channel B (usage page 0xFFF0) on 3537:1053 or
3537:100b. It checks every outgoing packet against an **allow-list** of
(opcode, argument-shape) pairs; there is no deny-list, so anything else is
refused.
- **Step 5 (reads)**, after the owner approves the first packet: `09`, `0B`,
  `04 bank off len` with bank ∈ {1–5, 0x20, 0x30} and off + len within the
  bank, `10` (same constraint), `0D` (no arguments), and `F2 00`.
- **Step 6 (writes)**, each new kind approved individually: `03` restricted
  to the field ranges in §4 that a command needs, never 0x039D–0x03A3;
  `07 n` with n ∈ 1–4.
- **Never:** everything in the forbidden table, plus any opcode not listed
  above. Nothing ever goes to the idle dongle (3537:0575) or its channel A.

## 3. Checksums / CRC

**None.** GSC's write path (`writeProfile`, l. ~15925) diffs the newly
serialized bank against the last one read and sends the raw changed bytes.
The serializers (`$a.fromModel`, `qo.fromModel`) compute no sum or CRC.
**high**

## 4. Settings → bytes

### 4.1 Banks
| Bank | Size | Contents |
|---|---|---|
| 1–4 | 680 | profiles 1–4 |
| 5 | 680 | "Shift" layer |
| 0x20 | 635 | Light (RGB), shared by all profiles |
| 0x30 | ? | current profile number at offset 0 (InFract; GSC uses 0x30 as "CurrentOfProfileNumber") |

### 4.2 Profile bank layout (680 bytes), from `$a.fromBytes`/`fromModel`

| Offset | Size | Field |
|---|---|---|
| 0x000 | 32 | profile name, UTF-8, NUL-padded |
| 0x020 | 32 | Fun_Data (§4.3) |
| 0x040 | 16 × 7 | key blocks (§4.4), in the order Up, Down, Left, Right, LB, RB, LS, RS, A, B, X, Y, Home, View, Menu, Share. Key *i* is at 0x040 + 7*i*. |
| 0x0B0 | 159 | L4 back button (§4.5) |
| 0x14F | 159 | R4 back button |
| 0x1EE | 28 | LT trigger (§4.6) |
| 0x20A | 28 | RT trigger |
| 0x226 | 32 | left stick (§4.7) |
| 0x246 | 32 | right stick |
| 0x266 | 33 | motion "Aim" (§4.8) |
| 0x287 | 33 | motion "Tilt" |

GSC names the face buttons the PlayStation way: CRO = A, CIR = B, SQU = X,
TRI = Y, PS = Home, Sel = View, Sta = Menu, Camera = Share, FL1/FR1 = L4/R4.

### 4.3 Fun_Data (0x020–0x03F)
| Offset | Field | Values |
|---|---|---|
| 0x020 / 0x021 / 0x022 / 0x023 | motor left / right / LT / RT | raw 0, 25, 50, 75, 100 = UI levels 0–4 (`D()`/`L()`, l. 8605). Default UI level 3 (raw 75). |
| 0x024–0x028 | audio: enable, volume, mixer, mic mute, mic sensitivity | |
| 0x029 / 0x02A | shift enable / shift value | |
| 0x02B | D-pad diagonal lock | bit 0 |
| 0x02C / 0x02D | XInput / Switch ABXY swap | bit 0 |
| 0x02E | report rate | 0 = 250 Hz, 1 = 500, 2 = 1000 (meaning from InFract; GSC passes the raw value) |
| 0x02F–0x03F | reserved | |

### 4.4 Key block (7 bytes)
`turbo_en, turbo_speed, map_en, map_value1, map_value2, map_value3, toggle_en`.
`map_value*` are key codes from GSC's table `Xa` (`getKeyMapValue` /
`getButtonKey`, **not yet extracted**: needed for remapping). Turbo:
`turbo_en = speed > 0`.

### 4.5 Back-button block (159 bytes)
The 7-byte key block, then macro data:
- flags byte: bit 0 enabled, bit 1 press-to-trigger, bit 2 cycle;
- cycle time (BE16), then step count (≤ 30);
- 30 steps of `key, hold BE16, delay BE16`. The last step has no delay.

### 4.6 Trigger block (28 bytes)
| +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 | +8–10 | +11 | +12 | +13 | +14 | +15 | +16 | +17 | +18–27 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| turbo_en | turbo_speed | dz_en | **dz_front** | **dz_back** | anti_front | anti_back | map_en | map_value1–3 | toggle | **hair: status** | hair start | hair end | curve_en | curve type | curvature | 5 × (orig, target) |

- Deadzones are stored as the UI percentages. GSC's defaults are
  5 / 95 / 0 / 100.
- Hair trigger status = `0x80 | mode` (mode 1 or 2), or 0 when off.
- So the **LT deadzone** is at 0x1F1 (front) and 0x1F2 (back), and the
  **RT deadzone** at 0x20D / 0x20E.

### 4.7 Stick block (32 bytes)
| +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 | +8 | +9 | +10–19 | +20 | +21 | +22 | +23 | +24 | +25 | +26 | +27–31 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stick_en | square | dz_en | **dz_front** | **dz_back** | anti_front | anti_back | curve_en | type | curvature | 5 × (orig, target) | map_en | x_flip | y_flip | axis_ratio | mouse_dpi | output | cross | up, down, left, right, outline |

- Deadzones are stored as percentages. GSC's defaults are 10 / 100 / 0 / 100.
- Curve points are scaled to 0–255 between the deadzones: point 1 is
  (0, 0), point 5 is (255, 255).
- So the **left-stick deadzone** is at 0x229 / 0x22A, and the **right-stick
  deadzone** at 0x249 / 0x24A.

### 4.8 Motion block (33 bytes)
`activate behaviour, quick key, active axis` (bit 0 / bit 1), followed by the
same deadzone, curve and map sub-blocks as a stick.

### 4.9 Light bank (0x20, 635 bytes), from `qo` (l. 15399)
| Offset | Field |
|---|---|
| 0x000 | active effect (0–3; GSC reads >3 as 0) |
| 0x001 + 124*k* | animation *k* (0–4) |
| 0x26D | audio-reactive (bit 0) |
| 0x26E | user effect index |
| 0x26F–0x271 | "ProfileLed" R, G, B |
| 0x272 | raise-to-wake (bit 0) |
| 0x273 | standby time |
| 0x274–0x27A | reserved |

Animation layout (124 bytes):
- `+0` keyframe count (1–8), `+1` effect count;
- `+2` **speed, stored as 20 − UI speed** (0–20);
- `+3` **brightness** (0–100);
- `+4 + 15f + 3z`: frame *f* (0–7), zone *z* (0–4), as R, G, B.

So animation 0, frame 0, zone 0 is at 0x005. That matches InFract
(0x0001 / 0x0003 / 0x0004 / 0x0005; animation 2 at 0x007D). **high**

Zone order is **unresolved**. GSC names the live-state zones Home,
LowerLeft, LowerRight, UpperLeft, UpperRight; InFract labels the frame zones
left, right, unknown, Home, dots. To settle with a capture.

### 4.10 Target settings → where they live
| Setting | Location | Confidence |
|---|---|---|
| RGB colour / brightness / speed | bank 0x20: active effect + animation fields | high (two sources) |
| Vibration strength | bank *n*, 0x020–0x023 | high |
| Stick deadzones | bank *n*, 0x229–0x22C (L) / 0x249–0x24C (R) | high (GSC only) |
| Trigger deadzones | 0x1F0–0x1F4 (LT) / 0x20C–0x210 (RT); hair trigger at +12–14 | high (GSC only) |
| Button remapping | key blocks 0x040–0x0AF, back buttons 0x0B0/0x14F; needs the `Xa` code table | medium |
| Profiles | read: `0B` / bank 0x30; switch: `07 n` | high |

## 5. Firmware-update paths (must never be triggered)

None of this is to be implemented. It is here so we know what to avoid.

- **Bundled images.** `resources/dfu/packages/` holds 48 `.ufw` images
  (quarantined locally). The catalogue links the Cyclone 2 to
  `GS_C2_DEVICE_V123` (older variant, PIDs 0x101D/0x102A),
  `GS_C2_ADC_DEVICE_V352` (our controller, PID 0x1053) and
  `GS_C2_Dongle_V121` (PID 0x100B). **high**
- **Decision.** `getDfuPackage` (l. 37406):
  - `hasUpdate` = the bundled version is newer than the device's;
  - `mustUpdateFirst = hasUpdate && current < MinimumVersion`. The Cyclone 2
    entries have no `MinimumVersion`, so it is **false**.
  - For our hardware (V3.4.6 / V1.2.0) GSC will show "update available" but
    will not demand it. **high**
  - `appConfig.enableFirmwareUpdate: false` exists but nothing reads it.
- **Triggers.** Only user actions, from static search of `renderer.js`:
  - the update dialog's `update()` (l. ~71527) → `reqQuickUpdate`;
  - `confirmUpdate()` (l. ~105486) → `reqQuickUpdateWithPrompt`.
  - `reqDfu` and `reqAutoDfu` have listeners in `main.js` but no emitter
    turned up in either bundle. Confidence **medium**: the static search
    cannot rule out dynamically built event names.
- **Mechanism.** For the Cyclone 2, `reqQuickUpdate` → `0F 17 55 88`.
  *Guess (medium):* the controller re-enumerates as a removable disk.
  `uDiskDfuUtil.ts` finds removable disks via PowerShell, then `wmic`, then
  by guessing drive letters D–J. It identifies the disk and flashes it
  through the JieLi DLL (`JL_queryDevicePidVid`, `JL_loadFirmware`). Chip
  types it tries: 695, AD160, AC632N (dongle), JL703N.
- **Consequence for us:**
  - Under Wine, PowerShell and `wmic` are missing, and the JieLi DLL needs
    raw disk access. An update started under Wine could leave the
    controller **stuck in update mode**, and GameSir's forced update
    (Logo + Menu) relies on the same disk path. **Wine is therefore not a
    recovery path.** A real Windows install (VM with USB passthrough, or a
    PC) is needed before the first write.
  - During captures: never click Update or accept any update prompt in GSC.
