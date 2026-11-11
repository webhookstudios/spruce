#!/bin/sh
# Builds dist/spruce_<version>_all.deb. Needs only dpkg-deb.
#
#   /usr/share/spruce/spruce/                    the app's code
#   /usr/bin/spruce                              launcher and command line
#   /usr/share/nautilus-python/extensions/       the Files right-click options
#   /usr/share/applications, /usr/share/icons    apps-menu entry and icon
set -e

APP_ID=io.github.webhookstudios.Spruce
ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/spruce/spruce" \
    "$STAGE/usr/share/nautilus-python/extensions" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/doc/spruce"
install -m 644 "$ROOT"/spruce/*.py "$STAGE/usr/share/spruce/spruce/"
install -m 755 "$ROOT/bin/spruce" "$STAGE/usr/bin/spruce"
install -m 644 "$ROOT/nautilus/spruce_nautilus.py" "$STAGE/usr/share/nautilus-python/extensions/"
install -m 644 "$ROOT/data/$APP_ID.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
sed -e 's|@EXEC@|spruce|' -e "s|@ICON@|$APP_ID|" "$ROOT/data/$APP_ID.desktop.in" \
    > "$STAGE/usr/share/applications/$APP_ID.desktop"
chmod 644 "$STAGE/usr/share/applications/$APP_ID.desktop"

cat > "$STAGE/usr/share/doc/spruce/copyright" <<EOT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Spruce
Source: https://github.com/webhookstudios/spruce

Files: *
Copyright: $(date +%Y) webhookstudios
License: GPL-2+
 On Debian systems, the full text of the GNU General Public License
 version 2 can be found in /usr/share/common-licenses/GPL-2.
EOT
chmod 644 "$STAGE/usr/share/doc/spruce/copyright"

cat > "$STAGE/DEBIAN/control" <<EOT
Package: spruce
Version: $VERSION
Architecture: all
Maintainer: webhookstudios <354619278+webhookstudios@users.noreply.github.com>
Installed-Size: $(du -sk "$STAGE/usr" | cut -f1)
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), python3-pil, python3-nautilus, libheif1
Recommends: libheif-plugin-aomenc, libheif-plugin-aomdec
Suggests: libheif-plugin-x265, libheif-plugin-libde265
Section: utils
Priority: optional
Homepage: https://github.com/webhookstudios/spruce
Description: Bulk rename, resize and convert from the Files right-click menu
 Spruce adds Bulk Rename, Resize Images and Convert Images to the right-click
 menu of the Files app. Bulk Rename has find and replace with regular
 expressions, letter case, numbering, date and photo tokens, a live preview
 that flags conflicts, and undo. The image tools resize to preset or custom
 sizes and convert between PNG, JPEG, WebP, AVIF and HEIC, making copies
 unless you choose to replace the originals.
 .
 After installing, run "nautilus -q" once so Files picks up the new options.
EOT

find "$STAGE" -type d -exec chmod 755 {} +
mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/spruce_${VERSION}_all.deb"
