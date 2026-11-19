#!/bin/bash
# Empaqueta dist/DEV Backup Studio (PyInstaller) como AppImage.
#   installer/linux/build-appimage.sh <versión> [appimagetool]
set -euo pipefail
version="$1"
tool="${2:-appimagetool}"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
appdir="$root/build/AppDir"
rm -rf "$appdir"
mkdir -p "$appdir/usr/lib"
cp -a "$root/dist/DEV Backup Studio" "$appdir/usr/lib/dev-backup-studio"
install -m 755 "$here/AppRun" "$appdir/AppRun"
cp "$here/dev-backup-studio.desktop" "$appdir/"
python3 - "$root/assets/icon.png" "$appdir/dev-backup-studio.png" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).resize((256, 256), Image.LANCZOS).save(sys.argv[2])
PY
ln -s dev-backup-studio.png "$appdir/.DirIcon"
out="$root/dist/DEV-Backup-Studio-$version-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$appdir" "$out"
chmod 755 "$out"
echo "$out"
