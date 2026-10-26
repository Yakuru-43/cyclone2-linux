#!/usr/bin/env bash
# Run GameSir Connect under Wine inside the cyclone2 distrobox.
#
#   tools/gamesir-wine.sh [--hid] [--net] [-- extra Electron args]
#
# The defaults are the safe ones:
#   - Wine's HID bus driver is not loaded, so GameSir Connect cannot see the
#     controller.
#   - The app has no network, so it cannot download firmware or update itself.
# --hid and --net each lift one restriction. Wine's raw USB driver stays off in
# every mode. Logs go to vendor/logs/.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP="$ROOT/vendor/app/GameSir Connect.exe"
ORIG_ARGS=("$@")
export WINEPREFIX=${WINEPREFIX:-$HOME/.local/share/wineprefixes/gamesir-connect}
export WINEDEBUG=${WINEDEBUG:--all}

hid=0 net=0
while [ $# -gt 0 ]; do
  case $1 in
    --hid) hid=1 ;;
    --net) net=1 ;;
    --) shift; break ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# GameSir Connect ships firmware images (resources/dfu/packages/*.ufw), so a
# blocked network alone does not stop it from flashing. They are kept in
# vendor/quarantine/ instead; refuse to run if any are back in the app tree.
if find "$ROOT/vendor/app" -iname '*.ufw' -print -quit | grep -q .; then
  echo "refusing to start: firmware images found under vendor/app" >&2
  exit 1
fi

if [ -z "${CONTAINER_ID:-}" ]; then
  exec distrobox enter cyclone2 -- "$0" "${ORIG_ARGS[@]}"
fi
if [ $net = 0 ] && [ -z "${CYCLONE2_NONET:-}" ]; then
  CYCLONE2_NONET=1 exec unshare --user --map-current-user --net -- "$0" "${ORIG_ARGS[@]}"
fi

# mscoree/mshtml: skip the Mono/Gecko install prompts when the prefix is created.
# sapjoyuniapis_x86_v1.0.16: GameSir's SDK for other controllers (T3P). It calls
# SHGetKnownFolderPath with CSIDL-style flags (0x8023), Wine returns
# E_INVALIDARG and the DLL then dereferences NULL, killing the app. Refusing to
# load it makes ffi throw inside a try/catch that GameSir already has.
# wineusb.sys: Wine's libusb driver. It otherwise opens every USB device the
# user can write to (keyboard, mouse receiver...). The controller is HID.
overrides='mscoree,mshtml=;sapjoyuniapis_x86_v1.0.16=d;wineusb.sys=d'
[ $hid = 1 ] || overrides+=';winebus.sys=d'
export WINEDLLOVERRIDES=$overrides

mkdir -p "$(dirname "$WINEPREFIX")"
wineserver -k 2>/dev/null || true
# With --hid, winebus is loaded: expose hidraw only (no evdev, no SDL remapping).
key='HKLM\System\CurrentControlSet\Services\winebus'
wine reg add "$key" /v DisableHidraw /t REG_DWORD /d $((1 - hid)) /f >/dev/null
wine reg add "$key" /v DisableInput /t REG_DWORD /d 1 /f >/dev/null
wine reg add "$key" /v "Enable SDL" /t REG_DWORD /d 0 /f >/dev/null
# winebus reads these at startup; restart the prefix so they apply.
wineserver -w

mkdir -p "$ROOT/vendor/logs"
log="$ROOT/vendor/logs/wine-$(date +%Y%m%d-%H%M%S).log"
echo "hid=$hid net=$net prefix=$WINEPREFIX log=$log"
wine "$APP" --no-sandbox --disable-gpu "$@" >"$log" 2>&1
