"""Lo que comparten las conexiones con servicios (Google Drive): secretos y carpetas con icono.

Secrets: tokens fuera de settings.ini. Windows: cifrados con DPAPI (solo tu usuario en este equipo
puede leerlos). macOS y Linux: archivo con permisos 0600.

ensure_folder(): una carpeta en la raíz del disco de copia con el icono del servicio en el Explorador
(desktop.ini), el Finder, Nautilus/Nemo/Caja (metadatos de GVFS) y Dolphin (.directory).
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
SPACE_RESERVE = 256 * 1024 * 1024  # lo que se deja libre de más en el disco de copia
SCHEDULES = ("manual", "daily", "weekly")
SCHEDULE_DAYS = {"manual": 0, "daily": 1, "weekly": 7}


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:80] or "plesk"


# ── secretos ───────────────────────────────────────────────────────────────

if IS_WIN:
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for _fn in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
        _fn.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
        _fn.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _UI_FORBIDDEN = 0x1

    def _dpapi(data: bytes, protect: bool) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        inp = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = _BLOB()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        if not fn(ctypes.byref(inp), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)


class Secrets:
    """Tokens y contraseñas, fuera de settings.ini. Windows: cifrados con DPAPI (solo tu usuario en
    este equipo puede leerlos). macOS y Linux: archivo con permisos 0600."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        if not IS_WIN:
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, key: str) -> str:
        with self._lock:
            value = self._load().get(key)
        if not isinstance(value, str) or ":" not in value:
            return ""
        how, blob = value.split(":", 1)
        try:
            raw = base64.b64decode(blob)
            if how == "dpapi" and IS_WIN:
                raw = _dpapi(raw, protect=False)
            elif how != "plain":
                return ""
            return raw.decode("utf-8")
        except (OSError, ValueError):
            return ""

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._load()
            if not value:
                data.pop(key, None)
            else:
                raw = value.encode("utf-8")
                how = "plain"
                if IS_WIN:
                    raw, how = _dpapi(raw, protect=True), "dpapi"
                data[key] = f"{how}:{base64.b64encode(raw).decode()}"
            self._save(data)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            data = self._load()
            for k in [k for k in data if k.startswith(prefix)]:
                del data[k]
            self._save(data)


# ── carpeta con icono ───────────────────────────────────────────────────────

def ensure_folder(root: str, assets: str, name: str, tip: str) -> str:
    """Crea la carpeta con su icono (Explorador, Finder, Nautilus y Dolphin): <assets>/<name>.ico y .png."""
    os.makedirs(root, exist_ok=True)
    try:
        if IS_WIN:
            _icon_windows(root, assets, name, tip)
        elif IS_MAC:
            _icon_mac(root, assets, name)
        else:
            _icon_linux(root, assets, name)
    except (OSError, subprocess.SubprocessError):
        pass  # sin icono la carpeta sirve igual
    return root


def _copy_if_changed(src: str, dst: str) -> bool:
    try:
        if os.path.getsize(src) == os.path.getsize(dst):
            with open(src, "rb") as a, open(dst, "rb") as b:
                if a.read() == b.read():
                    return False
    except OSError:
        pass
    if IS_WIN and os.path.exists(dst):
        ctypes.windll.kernel32.SetFileAttributesW(dst, 0x80)  # normal: si está oculto no se puede pisar
    shutil.copyfile(src, dst)
    return True


def _icon_windows(root: str, assets: str, name: str, tip: str) -> None:
    k32 = ctypes.windll.kernel32
    ico = os.path.join(root, f"{name}.ico")
    ini = os.path.join(root, "desktop.ini")
    changed = _copy_if_changed(os.path.join(assets, f"{name}.ico"), ico)
    text = (f"[.ShellClassInfo]\r\nIconResource={name}.ico,0\r\nIconFile={name}.ico\r\nIconIndex=0\r\n"
            f"InfoTip={tip}\r\n")
    try:
        with open(ini, encoding="utf-8") as fh:
            same = fh.read().replace("\n", "\r\n").replace("\r\r", "\r") == text
    except (OSError, UnicodeDecodeError):
        same = False
    if not same:
        if os.path.exists(ini):
            k32.SetFileAttributesW(ini, 0x80)
        with open(ini, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        changed = True
    hidden_system = 0x2 | 0x4
    k32.SetFileAttributesW(ico, hidden_system)
    k32.SetFileAttributesW(ini, hidden_system)
    # el Explorador solo lee desktop.ini de las carpetas marcadas como de sistema o de solo lectura
    attrs = k32.GetFileAttributesW(root)
    if attrs != 0xFFFFFFFF and not attrs & 0x1:
        k32.SetFileAttributesW(root, (attrs & ~0x80) | 0x1)
        changed = True
    if changed:
        shell32 = ctypes.windll.shell32
        shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        buf = ctypes.create_unicode_buffer(root)
        shell32.SHChangeNotify(0x00002000, 0x0005 | 0x1000, buf, None)  # SHCNE_UPDATEITEM, SHCNF_PATHW|FLUSHNOWAIT


_MAC_SET_ICON = """
ObjC.import("AppKit");
function run(argv) {
  var img = $.NSImage.alloc.initWithContentsOfFile(argv[0]);
  return $.NSWorkspace.sharedWorkspace.setIconForFileOptions(img, argv[1], 0) ? "ok" : "no";
}
"""


def _icon_mac(root: str, assets: str, name: str) -> None:
    marker = os.path.join(root, "Icon\r")  # el icono propio de una carpeta vive en este archivo
    if os.path.exists(marker):
        return
    subprocess.run(["osascript", "-l", "JavaScript", "-e", _MAC_SET_ICON, os.path.join(assets, f"{name}.png"), root],
                   capture_output=True, timeout=20)


def _icon_linux(root: str, assets: str, name: str) -> None:
    png = os.path.join(root, f".{name}.png")
    _copy_if_changed(os.path.join(assets, f"{name}.png"), png)
    # Dolphin (KDE)
    text = f"[Desktop Entry]\nIcon={png}\n"
    try:
        with open(os.path.join(root, ".directory"), encoding="utf-8") as fh:
            same = fh.read() == text
    except OSError:
        same = False
    if not same:
        with open(os.path.join(root, ".directory"), "w", encoding="utf-8") as fh:
            fh.write(text)
    # Nautilus, Nemo, Caja (GNOME y derivados): metadatos de GVFS
    gio = shutil.which("gio")
    if gio:
        subprocess.run([gio, "set", root, "metadata::custom-icon", "file://" + urllib.parse.quote(png)],
                       capture_output=True, timeout=10)
