"""Empaqueta la extensión de Plesk: plesk-extension/ → dist/DEV-Backup-Studio-Plesk-<versión>.zip

    python tools/build_plesk_extension.py          → crea el .zip e imprime su ruta
    python tools/build_plesk_extension.py --check  → solo comprueba que las versiones cuadran

Se instala en Plesk con Extensiones → Mis extensiones → Subir extensión, o con
«plesk bin extension --install <zip>» («--upgrade» para actualizarla).
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "plesk-extension")
EXECUTABLE = {"sbin/devbackup"}


def version() -> str:
    meta = ET.parse(os.path.join(SRC, "meta.xml")).getroot()
    ver = meta.findtext("version", "").strip()
    with open(os.path.join(SRC, "plib", "library", "Api.php"), encoding="utf-8") as fh:
        found = re.search(r"const VERSION = '([^']+)'", fh.read())
    if not ver or not found or found.group(1) != ver:
        raise SystemExit(f"versión distinta en meta.xml ({ver}) y Api.php ({found and found.group(1)})")
    return ver


def build(out_dir: str) -> str:
    ver = version()
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"DEV-Backup-Studio-Plesk-{ver}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _dirs, files in os.walk(SRC):
            for name in sorted(files):
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, SRC).replace(os.sep, "/")
                info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3  # Unix: conserva los permisos
                info.external_attr = (0o100755 if rel in EXECUTABLE else 0o100644) << 16
                with open(path, "rb") as fh:
                    data = fh.read()
                if not name.endswith(".png"):
                    data = data.replace(b"\r\n", b"\n")  # el script de sbin no puede llevar CRLF
                z.writestr(info, data)
    return out


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(version())
    else:
        print(build(os.path.join(ROOT, "dist")))
