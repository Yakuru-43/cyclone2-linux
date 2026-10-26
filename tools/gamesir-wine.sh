#!/usr/bin/env bash
# Run GameSir Connect under Wine inside the cyclone2 distrobox.
#
#   tools/gamesir-wine.sh [--hid /dev/hidrawN] [--net] [-- extra Electron args]
#
# GameSir Connect runs in its own user, mount and network namespaces:
#   - A private /dev with only the basics, so no real HID or USB device is
#     reachable. --hid adds one hidraw node, which must be a virtual (uhid)
#     device, i.e. the one tools/c2proxy.py creates.
#   - No network unless --net, so it cannot download firmware or update itself.
# Wine's raw USB driver stays off in every mode. Logs go to vendor/logs/.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SELF="$ROOT/tools/$(basename "$0")"
APP="$ROOT/vendor/app/GameSir Connect.exe"
ORIG_ARGS=("$@")
export WINEPREFIX=${WINEPREFIX:-$HOME/.local/share/wineprefixes/gamesir-connect}
export WINEDEBUG=${WINEDEBUG:--all}

hid="" net=0
while [ $# -gt 0 ]; do
  case $1 in
    --hid) hid=${2:?--hid needs a /dev/hidrawN path}; shift ;;
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

if [ -n "$hid" ]; then
  name=${hid#/dev/}
  sys=$(readlink -f "/sys/class/hidraw/$name/device" 2>/dev/null) || {
    echo "no such hidraw device: $hid" >&2; exit 1; }
  case $sys in
    */devices/virtual/*) ;;
    *) echo "refusing: $hid is a real device, not the proxy's virtual one" >&2; exit 1 ;;
  esac
fi

case ${CYCLONE2_STAGE:-host} in
  host)
    exec distrobox enter cyclone2 -- env CYCLONE2_STAGE=container "$SELF" "${ORIG_ARGS[@]}"
    ;;
  container)
    flags=(--user --map-root-user --mount)
    [ $net = 1 ] || flags+=(--net)
    exec unshare "${flags[@]}" -- env CYCLONE2_STAGE=sandbox CYCLONE2_UID="$(id -u)" \
      CYCLONE2_GID="$(id -g)" "$SELF" "${ORIG_ARGS[@]}"
    ;;
  sandbox)
    # Root inside our own user namespace: replace /dev with a private tmpfs
    # holding only what Wine needs, then drop back to the real uid.
    hostdev=$(mktemp -d /tmp/cyclone2-hostdev.XXXXXX)
    # Recursive: a user namespace may not bind /dev without its locked submounts.
    mount --rbind /dev "$hostdev"
    mount -t tmpfs -o mode=755 cyclone2-dev /dev
    for n in null zero full random urandom tty ptmx; do
      touch "/dev/$n"; mount --bind "$hostdev/$n" "/dev/$n"
    done
    mkdir /dev/pts /dev/shm
    mount --bind "$hostdev/pts" /dev/pts
    mount -t tmpfs -o mode=1777 cyclone2-shm /dev/shm
    if [ -d "$hostdev/dri" ]; then mkdir /dev/dri; mount --rbind "$hostdev/dri" /dev/dri; fi
    ln -s /proc/self/fd /dev/fd
    ln -s fd/0 /dev/stdin; ln -s fd/1 /dev/stdout; ln -s fd/2 /dev/stderr
    if [ -n "$hid" ]; then touch "$hid"; mount --bind "$hostdev/${hid#/dev/}" "$hid"; fi
    umount -l "$hostdev" && rmdir "$hostdev"
    exec unshare --user --map-user="$CYCLONE2_UID" --map-group="$CYCLONE2_GID" -- \
      env CYCLONE2_STAGE=wine "$SELF" "${ORIG_ARGS[@]}"
    ;;
  wine) ;;
  *) echo "bad CYCLONE2_STAGE" >&2; exit 2 ;;
esac

# mscoree/mshtml: skip the Mono/Gecko install prompts when the prefix is created.
# sapjoyuniapis_x86_v1.0.16: GameSir's SDK for other controllers (T3P). It calls
# SHGetKnownFolderPath with CSIDL-style flags (0x8023), Wine returns
# E_INVALIDARG and the DLL then dereferences NULL, killing the app. Refusing to
# load it makes ffi throw inside a try/catch that GameSir already has.
# wineusb.sys: Wine's libusb driver. It otherwise opens every USB device the
# user can write to (keyboard, mouse receiver...). The controller is HID.
overrides='mscoree,mshtml=;sapjoyuniapis_x86_v1.0.16=d;wineusb.sys=d'
[ -n "$hid" ] || overrides+=';winebus.sys=d'
export WINEDLLOVERRIDES=$overrides

mkdir -p "$(dirname "$WINEPREFIX")"
# A wineserver left over from another session would share its devices; stop it.
wineserver -k 2>/dev/null || true
# With --hid, winebus is loaded: expose hidraw only (no evdev, no SDL remapping).
key='HKLM\System\CurrentControlSet\Services\winebus'
wine reg add "$key" /v DisableHidraw /t REG_DWORD /d "$([ -n "$hid" ] && echo 0 || echo 1)" /f >/dev/null
wine reg add "$key" /v DisableInput /t REG_DWORD /d 1 /f >/dev/null
wine reg add "$key" /v "Enable SDL" /t REG_DWORD /d 0 /f >/dev/null
# winebus reads these at startup; restart the prefix so they apply.
wineserver -w

mkdir -p "$ROOT/vendor/logs"
log="$ROOT/vendor/logs/wine-$(date +%Y%m%d-%H%M%S).log"
echo "hid=${hid:-none} net=$net prefix=$WINEPREFIX log=$log"
wine "$APP" --no-sandbox --disable-gpu "$@" >"$log" 2>&1
