#!/bin/sh
# Installs Spruce for the current user, without root: the app, the Files
# right-click options (needs python3-nautilus) and an apps-menu entry.
# Run ./install.sh --uninstall to remove it.
set -e

APP_ID=io.github.webhookstudios.Spruce
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/spruce"
BIN="$HOME/.local/bin/spruce"
EXT="$DATA/nautilus-python/extensions/spruce_nautilus.py"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$DEST" "$BIN" "$EXT" \
        "$DATA/applications/$APP_ID.desktop" \
        "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
    update-desktop-database "$DATA/applications" 2>/dev/null || true
    echo "Spruce is removed. Run \"nautilus -q\" to drop its options from Files."
    exit 0
fi

if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); import PIL" 2>/dev/null; then
    cat >&2 <<EOM
Spruce needs Python 3 with GTK 4, libadwaita and Pillow. Install them, then
run this script again:

  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil
EOM
    exit 1
fi

mkdir -p "$DEST" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps" \
    "$(dirname "$EXT")" "$(dirname "$BIN")"
rm -rf "$DEST"
mkdir -p "$DEST/spruce"
cp "$ROOT"/spruce/*.py "$DEST/spruce/"
cp "$ROOT/bin/spruce" "$DEST/launcher.py"

# A small wrapper so "spruce rename FILE..." and friends work in a terminal too.
cat > "$BIN" <<EOT
#!/bin/sh
SPRUCE_DIR="$DEST" exec python3 "$DEST/launcher.py" "\$@"
EOT
chmod 755 "$BIN"
cp "$ROOT/nautilus/spruce_nautilus.py" "$EXT"

cp "$ROOT/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
ICON="$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
sed -e "s|@EXEC@|$BIN|" -e "s|@ICON@|$ICON|" \
    "$ROOT/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true

echo "Spruce is installed."
if python3 -c "import gi; gi.require_version('Nautilus', '4.1')" 2>/dev/null || \
   python3 -c "import gi; gi.require_version('Nautilus', '4.0')" 2>/dev/null; then
    echo "Run \"nautilus -q\" once (this closes open Files windows), then right-click some files."
else
    echo "For the Files right-click options, also run \"sudo apt install python3-nautilus\", then \"nautilus -q\"."
fi
