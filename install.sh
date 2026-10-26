#!/bin/sh
# Install cyclone2 for the current user: one file in ~/.local/bin plus a menu
# entry. No dependencies, no root. Needs Python 3.9+.
#   ./install.sh              install or update
#   ./install.sh --uninstall  remove
set -eu
here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/.local/bin/cyclone2"
desktop="$HOME/.local/share/applications/cyclone2.desktop"

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$bin" "$desktop"
    echo "removed $bin and $desktop"
    exit 0
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || {
    echo "cyclone2 needs Python 3.9 or newer" >&2; exit 1; }

python3 "$here/tools/build.py"
mkdir -p "$(dirname "$bin")" "$(dirname "$desktop")"
install -m 755 "$here/dist/cyclone2" "$bin"
cat > "$desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Cyclone 2
GenericName=Controller settings
Comment=Configure the GameSir Cyclone 2 controller
Exec=$bin gui
Icon=input-gaming
Terminal=false
Categories=Settings;HardwareSettings;Utility;
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$(dirname "$desktop")" >/dev/null 2>&1 || true

echo "installed $bin"
echo "menu entry: Cyclone 2   (or run: cyclone2 gui)"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
    echo "note: add ~/.local/bin to your PATH to run 'cyclone2' from a terminal" ;; esac

# Access check: the controller's configuration channel must be writable.
for d in /sys/class/hidraw/hidraw*; do
    [ -e "$d" ] || continue
    if grep -qi 'HID_ID=0003:00003537:0000\(1053\|100B\)' "$d/device/uevent" 2>/dev/null; then
        node="/dev/$(basename "$d")"
        if [ ! -w "$node" ]; then
            echo "note: $node is not writable by you. Install the udev rule:"
            echo "  sudo cp $here/udev/60-cyclone2.rules /etc/udev/rules.d/"
            echo "  sudo udevadm control --reload && sudo udevadm trigger"
        fi
    fi
done
