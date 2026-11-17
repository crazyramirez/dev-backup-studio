"""
Motor de sincronización de DEV Backup Studio.

Cómo decide qué ha cambiado (lo mismo que robocopy, rsync o FreeFileSync en su
modo por defecto): un archivo es distinto si cambia el tamaño o la fecha de
modificación. En NTFS la fecha se compara exacta, al nanosegundo, porque
CopyFileExW la conserva tal cual; en FAT/exFAT se admite el margen de 2 s de
esos sistemas. Así se detecta también una edición que no altera el tamaño.

Qué hace con cada diferencia:
- nuevo o modificado  → se copia a un temporal y se renombra al terminar;
- modificado          → la versión anterior del destino se guarda en versiones;
- borrado del origen  → (modo espejo) el archivo del destino pasa a versiones;
- movido o renombrado → se mueve dentro del destino en vez de copiarse de nuevo.

Las versiones van a <unidad>\\.devbackup\\versiones\\<fecha>\\..., en el mismo
disco, así que guardarlas es solo un renombrado: nunca se pierde nada.

Para no releer todo el disco USB cada día, al terminar se guarda un índice
SQLite con el estado del destino. El siguiente análisis compara el origen con
ese índice y solo toca el disco para lo que cambió. Si el índice falta, cambió
la configuración o hace más de 7 días de la última revisión completa, se lee el
destino entero.

Integridad: cada archivo copiado se vuelve a leer del disco sin pasar por la
caché del sistema y su SHA-256 se compara con el del original; si no coincide
se repite la copia. El hash queda en el índice y, tras cada sincronización, se
revisa una tanda de archivos del destino (primero los nunca comprobados, luego
los más antiguos). Con el hash guardado se sabe qué lado se ha estropeado: si es
el destino se repara desde el original; si es el original, se avisa y la copia
buena no se toca.

Funciona en Windows (CopyFileExW, lectura sin caché con FILE_FLAG_NO_BUFFERING)
y en macOS/Linux (copia por bloques, F_NOCACHE en macOS).
"""

from __future__ import annotations

import ctypes
import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import uuid

from i18n import N_, _, _n
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
TMP_SUFFIX = ".bkpart"
CHUNK = 4 * 1024 * 1024
_SECTOR = 4096  # alineación de las lecturas sin caché
# Los archivos grandes se copian de uno en uno: en un disco mecánico varias
# escrituras grandes a la vez solo provocan saltos del cabezal.
BIG_FILE = 64 * 1024 * 1024
META_DIR = ".devbackup"
FULL_CHECK_DAYS = 7
# Por debajo de este tamaño un renombrado solo se reconoce si conserva el nombre.
MOVE_MIN_SIZE = 64 * 1024
# precisión de la fecha de modificación según el sistema de archivos del destino
TOLERANCE_NS = {"FAT": 2 * 10**9, "FAT32": 2 * 10**9, "EXFAT": 2 * 10**9, "MSDOS": 2 * 10**9,
                "VFAT": 2 * 10**9, "HFS": 10**9}
FAT_FAMILY = {"FAT", "FAT32", "EXFAT", "MSDOS", "VFAT"}
STAMP_FMT = "%Y-%m-%d %H%M%S"
ROOT_SKIP = {META_DIR, "system volume information", "$recycle.bin", ".spotlight-v100", ".fseventsd",
             ".trashes", ".temporaryitems", ".documentrevisions-v100"}
INDEX_SCHEMA = "2"
SCRUB_THREADS = 4
# La revisión de integridad no vuelve a leer lo comprobado hace menos de esto
# (al copiar ya se verifica). «Comprobar todo el disco» no tiene en cuenta el plazo.
SCRUB_RECHECK_DAYS = 30
# cuando vuelve un disco que había desaparecido, margen para que termine de montarse
DISK_SETTLE = 3.0

MODE_MIRROR = "mirror"
MODE_UPDATE = "update"
MODES = {  # se traducen al enseñarlos: _(MODES[modo])
    MODE_MIRROR: N_("Sincronizar (espejo con versiones)"),
    MODE_UPDATE: N_("Solo añadir y actualizar"),
}
LEGACY_MODES = {"same_size": MODE_MIRROR, "always": MODE_MIRROR, "newer": MODE_MIRROR}


def lp(path: str) -> str:
    """Ruta con prefijo \\\\?\\ para superar el límite de 260 caracteres de Windows."""
    if not IS_WIN:
        return path
    if path.startswith("\\\\?\\"):
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def norm_rel(rel: str) -> str:
    return os.path.normcase(os.path.normpath(rel)) if rel else ""


def match_rule(name: str, patterns: list[str]) -> str | None:
    """Devuelve la regla que excluye esta carpeta, o None."""
    low = name.lower()
    for pat in patterns:
        if fnmatch.fnmatchcase(low, pat):
            return pat
    return None


def is_link(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction and is_junction())


def is_unc(path: str) -> bool:
    """¿Ruta de red de Windows («\\\\servidor\\recurso\\...»)?"""
    return IS_WIN and path.startswith(("\\\\", "//")) and not path.startswith("\\\\?\\")


def volume_root(path: str) -> str:
    """Raíz de la unidad que contiene la ruta: «E:\\» o «\\\\servidor\\recurso\\» en Windows,
    «/Volumes/X» en macOS."""
    if IS_WIN:
        return os.path.splitdrive(os.path.abspath(path))[0].rstrip("\\/") + "\\"
    p = os.path.abspath(path)
    while not os.path.ismount(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def rel_to_volume(path: str) -> str:
    """Ruta dentro de su unidad: «E:\\DEV» → «DEV»."""
    rel = os.path.relpath(os.path.abspath(path), volume_root(path))
    return "" if rel == "." else rel


def volume_present(path: str) -> bool:
    """¿Está conectada la unidad de esta ruta?

    En macOS un disco desconectado deja «/Volumes/X» sin montar y escribir ahí
    llenaría el disco del sistema, así que se exige que sea un punto de montaje.
    """
    if IS_WIN:
        root = volume_root(path)
        host = network_host(root)
        if host is not None and not host_reachable(host):
            return False
        return os.path.exists(root)
    parts = os.path.abspath(path).split(os.sep)
    if len(parts) > 2 and parts[1] in ("Volumes", "media", "mnt"):
        top = os.sep.join(parts[:3 if parts[1] != "media" else 4])
        host = network_host(top)
        if host is not None and not host_reachable(host):
            return False
        return os.path.ismount(top)
    return True


def vol_name(root: str) -> str:
    if is_unc(root):
        return root.rstrip("\\/")
    return root[:2] if IS_WIN else (os.path.basename(root.rstrip("/")) or root)


# ── carpetas de red (SMB) ───────────────────────────────────────────────────
#
# Un destino puede ser una carpeta compartida de otro equipo de la red: «\\nube\Copias» o una
# unidad conectada («Z:») en Windows, o un recurso montado en /Volumes en macOS. Para el motor es
# un disco más; la diferencia es que, si el servidor está apagado, mirar la ruta puede dejar el
# programa esperando 20-60 s. Por eso antes de tocarla se comprueba con una conexión TCP de un
# segundo que el servidor responde.

NET_FS = {"SMBFS", "CIFS", "SMB3", "SMB2", "NFS", "NFS4", "AFPFS", "WEBDAV"}
SMB_PORT = 445
_NET_PROBE_TIMEOUT = 1.0
_NET_CACHE_S = 3.0
_net_cache: dict[str, tuple[float, bool]] = {}
_net_lock = threading.Lock()
_host_cache: dict[str, tuple[float, str | None]] = {}

if IS_WIN:
    _GetDriveTypeW = ctypes.WinDLL("kernel32").GetDriveTypeW
    _GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    _GetDriveTypeW.restype = ctypes.c_uint
    DRIVE_REMOTE = 4
    try:
        _WNetGetConnectionW = ctypes.WinDLL("mpr").WNetGetConnectionW
        _WNetGetConnectionW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        _WNetGetConnectionW.restype = ctypes.c_ulong
    except OSError:  # pragma: no cover - mpr.dll está en todos los Windows de escritorio
        _WNetGetConnectionW = None


def _unc_host(unc: str) -> str:
    return unc.replace("/", "\\").lstrip("\\").split("\\", 1)[0]


def network_host(root: str) -> str | None:
    """Servidor del que cuelga la unidad, o None si es un disco local."""
    now = time.monotonic()
    hit = _host_cache.get(root)
    if hit and now - hit[0] < 60:
        return hit[1]
    host = None
    if IS_WIN:
        if is_unc(root):
            host = _unc_host(root)
        elif len(root) >= 2 and root[1] == ":" and _GetDriveTypeW(root[:2] + "\\") == DRIVE_REMOTE:
            host = ""  # unidad de red; si no se sabe el servidor, se mira la ruta sin comprobarlo
            if _WNetGetConnectionW is not None:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_ulong(len(buf))
                if _WNetGetConnectionW(root[:2], buf, ctypes.byref(size)) == 0:
                    host = _unc_host(buf.value)
    else:
        line = _unix_mount_line(root)
        if line and _unix_fs_type(root).upper() in NET_FS:
            # macOS: «//usuario@nube._smb._tcp.local/Copias on /Volumes/Copias (smbfs, …)»
            src = line.split(" on ", 1)[0]
            host = src.lstrip("/").split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
            if host.endswith("._smb._tcp.local"):
                host = host[:-len("._smb._tcp.local")] + ".local"
    _host_cache[root] = (now, host)
    return host


def is_network(path: str) -> bool:
    return network_host(volume_root(path)) is not None


def host_reachable(host: str, port: int = SMB_PORT) -> bool:
    """¿Responde el servidor? Conexión TCP de como mucho un segundo; el resultado vale 3 s."""
    if not host:
        return True
    import socket
    now = time.monotonic()
    with _net_lock:
        hit = _net_cache.get(host)
        if hit and now - hit[0] < _NET_CACHE_S:
            return hit[1]
    ok = False
    try:
        for family, kind, proto, _canon, addr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            try:
                with socket.socket(family, kind, proto) as sock:
                    sock.settimeout(_NET_PROBE_TIMEOUT)
                    sock.connect(addr)
                    ok = True
                    break
            except OSError:
                continue
    except OSError:  # nombre que no se resuelve
        ok = False
    with _net_lock:
        _net_cache[host] = (time.monotonic(), ok)
    return ok


class _Cancelled(Exception):
    pass


if IS_WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROGRESS = ctypes.WINFUNCTYPE(
        wintypes.DWORD,
        ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.LPVOID,
    )
    _CopyFileExW = _k32.CopyFileExW
    _CopyFileExW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, _PROGRESS, wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL), wintypes.DWORD,
    ]
    _CopyFileExW.restype = wintypes.BOOL
    _GetVolumeInformationW = _k32.GetVolumeInformationW
    _GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
    ]
    _GetVolumeInformationW.restype = wintypes.BOOL
    _GetLogicalDrives = _k32.GetLogicalDrives
    _GetLogicalDrives.restype = wintypes.DWORD
    _SetFileAttributesW = _k32.SetFileAttributesW
    _SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _CreateFileW = _k32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _ReadFile = _k32.ReadFile
    _ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                          wintypes.LPVOID]
    _ReadFile.restype = wintypes.BOOL
    _CloseHandle = _k32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _VirtualAlloc = _k32.VirtualAlloc
    _VirtualAlloc.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    _VirtualAlloc.restype = wintypes.LPVOID
    _VirtualFree = _k32.VirtualFree
    _VirtualFree.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]
    INVALID_HANDLE = wintypes.HANDLE(-1).value
    PROGRESS_CONTINUE, PROGRESS_CANCEL = 0, 1
    ERROR_PATH_NOT_FOUND, ERROR_ACCESS_DENIED, ERROR_REQUEST_ABORTED = 3, 5, 1235
else:
    PROGRESS_CONTINUE, PROGRESS_CANCEL = 0, 1


def _unix_mount_line(root: str) -> str:
    """Línea de «mount» del punto de montaje, o "" si no lo es."""
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if f" on {root} (" in line or f" on {root} type " in line:
            return line
    return ""


def _unix_fs_type(root: str) -> str:
    """Sistema de archivos de un punto de montaje según «mount» (apfs, exfat, msdos, smbfs…)."""
    line = _unix_mount_line(root)
    # macOS: «/dev/disk4s1 on /Volumes/X (exfat, local, …)»; Linux: «… on /media/x type vfat (…)»
    if f" on {root} (" in line:
        return line.split(f" on {root} (", 1)[1].split(",")[0].strip()
    if f" on {root} type " in line:
        return line.split(f" on {root} type ", 1)[1].split()[0]
    return ""


def volume_info(root: str) -> tuple[str, int, str]:
    """(etiqueta, número de serie, sistema de archivos) de una unidad."""
    if not IS_WIN:
        try:
            serial = os.stat(root).st_dev
        except OSError:
            return "", 0, ""
        return vol_name(root), serial, _unix_fs_type(root)
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    if not _GetVolumeInformationW(root, label, 261, ctypes.byref(serial), None, None, fs, 261):
        return "", 0, ""
    return label.value, serial.value, fs.value


def mounted_volumes(network: bool = True, extra=()) -> set[str]:
    """Unidades conectadas: {"C:\\", "E:\\"} en Windows, {"/Volumes/X"} en macOS.

    Las de red solo cuentan si su servidor responde (con network=False no se miran: así no se
    espera a la red). extra: raíces de carpetas de red («\\\\nube\\Copias\\») que también hay
    que comprobar, porque no tienen letra de unidad.
    """
    if IS_WIN:
        mask = _GetLogicalDrives()
        found = set()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = f"{chr(65 + i)}:\\"
            if network_host(root) is None:
                found.add(root)
            elif network and volume_present(root):
                found.add(root)
        if network:
            found |= {r for r in extra if volume_present(r)}
        return found
    bases = ["/Volumes"] if IS_MAC else [f"/media/{os.environ.get('USER', '')}", "/media", "/mnt"]
    found = set()
    for base in bases:
        try:
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if os.path.islink(p):
                    continue
                host = network_host(p)
                if host is not None and not (network and host_reachable(host)):
                    continue  # un recurso de red caído puede colgar ismount
                if os.path.ismount(p):
                    found.add(p)
        except OSError:
            pass
    return found


# ── lectura y hash ──────────────────────────────────────────────────────────

class ReadBuffer:
    """Búfer de lectura; en Windows alineado a página, como exige la lectura sin caché."""

    def __init__(self, size: int = CHUNK):
        self.size = size
        self.addr = None
        if IS_WIN:
            self.addr = _VirtualAlloc(None, size, 0x3000, 0x04)  # MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE
            if not self.addr:
                raise MemoryError("VirtualAlloc")
            self.view = memoryview((ctypes.c_char * size).from_address(self.addr))
        else:
            self._buf = bytearray(size)
            self.view = memoryview(self._buf)

    def free(self) -> None:
        if IS_WIN and self.addr:
            self.view.release()
            _VirtualFree(self.addr, 0, 0x8000)  # MEM_RELEASE
            self.addr = None


def file_digest(path: str, rb: ReadBuffer, uncached: bool = False, stop: threading.Event | None = None) -> bytes:
    """SHA-256 del archivo. Con uncached lee del disco, no de la caché del sistema."""
    h = hashlib.sha256()
    if IS_WIN:
        flags = 0x08000000 | (0x20000000 if uncached else 0)  # SEQUENTIAL_SCAN | NO_BUFFERING
        handle = _CreateFileW(lp(path), 0x80000000, 0x7, None, 3, flags, None)
        if handle is None or handle == INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        # Por SMB, una lectura sin caché que pasa del final del archivo da «parámetro incorrecto»
        # (87); en un disco local devuelve lo que queda. En red se lee sin caché solo la parte
        # múltiplo de 4 KB y la cola, más corta, se lee normal.
        remaining = os.stat(lp(path)).st_size if uncached and is_network(path) else -1
        offset = 0
        try:
            got = wintypes.DWORD()
            while True:
                if stop is not None and stop.is_set():
                    raise _Cancelled
                want = rb.size
                if remaining >= 0:
                    want = min(rb.size, remaining // _SECTOR * _SECTOR)
                    if not want:
                        break
                if not _ReadFile(handle, rb.addr, want, ctypes.byref(got), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not got.value:
                    break
                h.update(rb.view[:got.value])
                offset += got.value
                if remaining >= 0:
                    remaining -= got.value
        finally:
            _CloseHandle(handle)
        if remaining > 0:
            with open(lp(path), "rb", buffering=0) as fh:
                fh.seek(offset)
                h.update(fh.read())
        return h.digest()
    with open(path, "rb", buffering=0) as fh:
        if uncached and IS_MAC:
            try:
                import fcntl
                fcntl.fcntl(fh.fileno(), fcntl.F_NOCACHE, 1)
            except (ImportError, OSError, AttributeError):
                pass
        while True:
            if stop is not None and stop.is_set():
                raise _Cancelled
            n = fh.readinto(rb.view)
            if not n:
                break
            h.update(rb.view[:n])
    return h.digest()


# ── cambios pendientes de copiar (recordatorios) ────────────────────────────

def _touched(st: os.stat_result) -> float:
    """Última vez que se escribió o se creó el archivo. Un archivo copiado conserva su fecha de
    modificación antigua, pero su fecha de creación es la de la copia."""
    t = st.st_mtime
    birth = getattr(st, "st_birthtime", None)
    if birth:
        t = max(t, birth)
    elif IS_WIN:
        t = max(t, st.st_ctime)  # en Windows (Python < 3.12) st_ctime es la fecha de creación
    return t


def changes_since(src: str, since: float, patterns=(), excluded=(), cancel: threading.Event | None = None,
                  limit: int = 0) -> dict:
    """Lo que ha cambiado en el origen después de `since` (segundos desde 1970) sin abrir ningún archivo.

    Cuenta los archivos nuevos o modificados y las carpetas cuya fecha cambió, que es donde se notan
    los borrados, renombrados y movidos. Respeta las mismas exclusiones que la copia. Con `limit` > 0
    para en cuanto encuentra ese número de cambios.
    """
    out = {"files": 0, "bytes": 0, "dirs": 0, "scanned": 0, "newest": 0.0, "latest": "",
           "missing": False, "complete": True}
    try:
        root_st = os.stat(lp(src))
    except OSError:
        out["missing"] = True
        return out
    if not stat.S_ISDIR(root_st.st_mode):
        out["missing"] = True
        return out
    if _touched(root_st) > since:
        out["dirs"] += 1
    pats = sorted({p.strip().lower() for p in patterns if p.strip()})
    excl = {norm_rel(p) for p in excluded}
    stack = [""]
    while stack:
        if cancel is not None and cancel.is_set():
            out["complete"] = False
            break
        rel = stack.pop()
        try:
            with os.scandir(lp(os.path.join(src, rel) if rel else src)) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                if is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    child = os.path.join(rel, name) if rel else name
                    if ((not rel and name.lower() in ROOT_SKIP) or match_rule(name, pats)
                            or norm_rel(child) in excl):
                        continue
                    stack.append(child)
                    if _touched(entry.stat(follow_symlinks=False)) > since:
                        out["dirs"] += 1
                elif entry.is_file(follow_symlinks=False) and not name.endswith(TMP_SUFFIX):
                    out["scanned"] += 1
                    st = entry.stat(follow_symlinks=False)
                    t = _touched(st)
                    if t > since:
                        out["files"] += 1
                        out["bytes"] += st.st_size
                        if t > out["newest"]:
                            out["newest"] = t
                            out["latest"] = os.path.join(rel, name) if rel else name
            except OSError:
                continue
        if limit and out["files"] + out["dirs"] >= limit:
            out["complete"] = False
            break
    return out


# ── identidad del disco e historial ─────────────────────────────────────────

def read_disk_id(root: str) -> str | None:
    try:
        with open(os.path.join(root, META_DIR, "disco.json"), encoding="utf-8") as fh:
            return json.load(fh).get("id")
    except (OSError, ValueError, AttributeError):
        return None


def ensure_disk_id(root: str) -> str:
    """Marca el disco como disco de copia (o devuelve la marca que ya tiene)."""
    found = read_disk_id(root)
    if found:
        return found
    meta = os.path.join(root, META_DIR)
    os.makedirs(meta, exist_ok=True)
    if IS_WIN:
        _SetFileAttributesW(meta, 0x2)  # oculta
    data = {"id": uuid.uuid4().hex, "creado": datetime.now().isoformat(timespec="seconds"),
            "etiqueta": volume_info(root)[0]}
    with open(os.path.join(meta, "disco.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data["id"]


def append_history(root: str, entry: dict) -> None:
    try:
        with open(os.path.join(root, META_DIR, "historial.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def last_history(root: str) -> dict | None:
    try:
        with open(os.path.join(root, META_DIR, "historial.jsonl"), encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


# ── utilidades de archivos ──────────────────────────────────────────────────

def _make_writable(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _remove_quiet(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        _make_writable(path)
        try:
            os.remove(path)
        except OSError:
            pass


def _rmtree_force(path: str) -> None:
    def onexc(func, p, _exc):
        _make_writable(p)
        func(p)
    shutil.rmtree(lp(path), onexc=onexc)


def _split_rel(rel: str) -> tuple[str, str]:
    """«a\\b\\c.js» → («a\\b», «c.js»)."""
    head, tail = os.path.split(rel)
    return head, tail


# ── índice del destino ──────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE dirs(key TEXT PRIMARY KEY, path TEXT, parent TEXT) WITHOUT ROWID;
CREATE INDEX dirs_parent ON dirs(parent);
CREATE TABLE files(dir TEXT, name TEXT, size INTEGER, mtime INTEGER, hash BLOB, verified REAL,
                   PRIMARY KEY(dir, name)) WITHOUT ROWID;
"""


class IndexWriter:
    """Índice nuevo que se escribe junto al actual y lo sustituye al terminar."""

    def __init__(self, path: str):
        self.path = path
        self.tmp = f"{path}.{uuid.uuid4().hex[:8]}.new"
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        # restos de análisis que no llegaron a copiarse
        base = os.path.basename(path)
        for name in os.listdir(folder):
            if name.startswith(base + ".") and name.endswith(".new"):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass  # lo tiene abierto otro análisis
        self.con = sqlite3.connect(self.tmp, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=OFF")
        self.con.execute("PRAGMA synchronous=OFF")
        self.con.executescript(_SCHEMA)
        self._files: list[tuple] = []

    def add_file(self, rel_dir: str, name: str, size: int, mtime: int,
                 digest: bytes | None = None, verified: float | None = None) -> None:
        self._files.append((norm_rel(rel_dir), name, size, mtime, digest, verified))
        if len(self._files) >= 20000:
            self.flush()

    def flush(self) -> None:
        if self._files:
            self.con.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", self._files)
            self._files.clear()

    def commit(self, dirs: list[str], meta: dict) -> None:
        self.flush()
        rows = [("", "", None)]
        for rel in dirs:
            if rel:
                rows.append((norm_rel(rel), rel, norm_rel(os.path.dirname(rel))))
        self.con.executemany("INSERT OR REPLACE INTO dirs VALUES (?,?,?)", rows)
        self._carry_hashes()
        meta = dict(meta, schema=INDEX_SCHEMA)
        self.con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
        self.con.commit()
        self.con.close()
        os.replace(self.tmp, self.path)

    def _carry_hashes(self) -> None:
        """Conserva los hashes y fechas de revisión de los archivos que no han cambiado."""
        if not os.path.exists(self.path):
            return
        try:
            self.con.commit()
            self.con.execute("ATTACH DATABASE ? AS old", (self.path,))
            try:
                row = self.con.execute("SELECT v FROM old.meta WHERE k='schema'").fetchone()
                if row and row[0] == INDEX_SCHEMA:
                    self.con.execute(
                        "UPDATE files SET hash = o.hash, verified = o.verified FROM old.files AS o "
                        "WHERE files.hash IS NULL AND o.hash IS NOT NULL AND o.dir = files.dir "
                        "AND o.name = files.name AND o.size = files.size AND o.mtime = files.mtime")
                    self.con.commit()
            finally:
                self.con.execute("DETACH DATABASE old")
        except sqlite3.Error:
            pass

    def abort(self) -> None:
        try:
            self.con.close()
            os.remove(self.tmp)
        except (sqlite3.Error, OSError):
            pass


class IndexReader:
    def __init__(self, path: str):
        self.con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        self.meta = dict(self.con.execute("SELECT k, v FROM meta"))

    def has_dir(self, rel: str) -> bool:
        return self.con.execute("SELECT 1 FROM dirs WHERE key=?", (norm_rel(rel),)).fetchone() is not None

    def files(self, rel: str) -> dict[str, tuple[str, int, int]]:
        cur = self.con.execute("SELECT name, size, mtime FROM files WHERE dir=?", (norm_rel(rel),))
        return {os.path.normcase(n): (n, s, m) for n, s, m in cur}

    def subdirs(self, rel: str) -> dict[str, str]:
        cur = self.con.execute("SELECT path FROM dirs WHERE parent=?", (norm_rel(rel),))
        return {os.path.normcase(os.path.basename(p)): os.path.basename(p) for (p,) in cur}

    def close(self) -> None:
        self.con.close()


# ── estadísticas ────────────────────────────────────────────────────────────

@dataclass
class Stats:
    # análisis
    dirs_scanned: int = 0
    files_found: int = 0
    bytes_found: int = 0
    excluded_rule: int = 0
    excluded_manual: int = 0
    links_skipped: int = 0
    skip_files: int = 0      # sin cambios
    skip_bytes: int = 0
    new_files: int = 0
    new_bytes: int = 0
    changed_files: int = 0
    changed_bytes: int = 0
    del_files: int = 0
    del_bytes: int = 0
    moved_files: int = 0
    moved_bytes: int = 0
    plan_files: int = 0      # copias: nuevos + modificados
    plan_bytes: int = 0
    dirs_to_create: int = 0
    orphan_dirs: int = 0
    junk_files: int = 0
    fast: bool = False       # análisis con el índice, sin leer el destino
    current_dir: str = ""
    # ejecución
    step: str = ""
    files_done: int = 0
    bytes_done: int = 0
    new_done: int = 0          # de files_done: nuevos y modificados
    new_done_bytes: int = 0
    changed_done: int = 0
    changed_done_bytes: int = 0
    files_failed: int = 0
    bytes_failed: int = 0
    moves_done: int = 0
    deletes_done: int = 0
    other_failed: int = 0
    versions_saved: int = 0
    versions_purged: int = 0
    dirs_created: int = 0
    dirs_removed: int = 0
    errors: int = 0
    # integridad
    verified_copies: int = 0   # copias releídas del disco y comprobadas
    verify_retries: int = 0
    scrub_files: int = 0       # archivos del destino revisados en esta ejecución
    scrub_bytes: int = 0
    scrub_repaired: int = 0
    scrub_alerts: int = 0
    scrub_total: int = 0       # archivos en el índice
    scrub_covered: int = 0     # de ellos, comprobados alguna vez
    scrub_start: float = 0.0
    scrub_due: int = 0         # archivos pendientes de revisión al empezar
    # disco de copia que desaparece a mitad (suspensión, cable, reinicio del USB)
    disk_waits: int = 0        # veces que se ha esperado a que volviera
    disk_wait_s: float = 0.0   # tiempo total esperando
    disk_lost_at: float = 0.0  # perf_counter desde que falta; 0 = conectado
    # tiempos (perf_counter)
    scan_start: float = 0.0
    scan_end: float = 0.0
    copy_start: float = 0.0
    copy_end: float = 0.0

    @property
    def ops_total(self) -> int:
        return self.plan_files + self.moved_files + self.del_files

    @property
    def ops_done(self) -> int:
        return self.files_done + self.files_failed + self.moves_done + self.deletes_done + self.other_failed

    @property
    def has_changes(self) -> bool:
        return bool(self.ops_total or self.junk_files)


class BackupJob:
    """Una ejecución de sincronización. Se controla desde otro hilo; la UI solo lee."""

    def __init__(self, src: str, dst: str, patterns: list[str], excluded: set[str] | list[str],
                 mode: str = MODE_MIRROR, threads: int = 16, index_path: str | None = None,
                 force_full: bool = False, keep_days: int = 90, meta_root: str | None = None,
                 verify: bool = True, scrub_seconds: int = 300):
        self.src = os.path.normpath(src)
        self.dst = os.path.normpath(dst)
        self.patterns = sorted({p.strip().lower() for p in patterns if p.strip()})
        self.excluded = {norm_rel(p) for p in excluded}
        self.mode = LEGACY_MODES.get(mode, mode)
        self.threads = max(1, int(threads))
        self.index_path = index_path
        self.force_full = force_full
        self.keep_days = keep_days
        # versiones, marca e historial; tiene que estar en la misma unidad que el destino
        self.meta_root = meta_root or volume_root(self.dst)
        self.verify = verify
        self.scrub_seconds = scrub_seconds  # 0 = no revisar, -1 = sin límite de tiempo

        fs = volume_info(volume_root(self.dst))[2].upper()
        self.network = is_network(self.dst)
        self.tolerance_ns = TOLERANCE_NS.get(fs, 0)
        if self.network:
            # cada servidor guarda la fecha con su propia precisión (y no siempre dice cuál)
            self.tolerance_ns = max(self.tolerance_ns, 2 * 10**9)
        self.fat = fs in FAT_FAMILY

        self.stats = Stats()
        self.phase = "idle"  # idle | scanning | ready | copying | done | cancelled | failed
        self.fatal = ""
        self.errors: list[tuple[str, str]] = []
        self.pruned: list[tuple[str, str]] = []
        self.active: dict[int, list] = {}  # hilo -> [ruta, tamaño, transferido]
        self.summary: dict = {}
        self.scan_started_at: datetime | None = None
        self._reset_plan()

        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._scrub_stop = threading.Event()  # fin de la revisión: tiempo agotado o cancelar
        self._resume = threading.Event()
        self._resume.set()
        self._big = threading.Semaphore(1)
        self._next = 0
        self._disk_waiters = 0

    def _reset_plan(self) -> None:
        # (ruta origen, tamaño, fecha_ns, «n» nuevo | «c» cambiado, estado anterior en destino)
        self.copies: list[tuple[str, int, int, str, tuple | None]] = []
        # (ruta destino, tamaño, fecha_ns)
        self.deletes: list[tuple[str, int, int]] = []
        # (ruta destino antigua, índice en copies del archivo nuevo)
        self.moves: list[tuple[str, tuple]] = []
        self.dirs: list[str] = []          # carpetas que hay que crear en el destino
        self.orphan_dirs: list[str] = []   # carpetas del destino que ya no existen en el origen
        self.junk: list[str] = []          # temporales de ejecuciones interrumpidas
        self._seen_dirs: list[str] = []
        self._copy_ok: bytearray = bytearray()
        self._delete_ok: bytearray = bytearray()
        self._move_ok: bytearray = bytearray()
        self._removed_dirs: set[str] = set()
        self._writer: IndexWriter | None = None
        self._reader: IndexReader | None = None
        self.index_note = ""

    # ── control ──────────────────────────────────────────────────────────
    def signature(self) -> tuple:
        return (os.path.normcase(self.src), os.path.normcase(self.dst), tuple(self.patterns),
                tuple(sorted(self.excluded)), self.mode, self.force_full)

    def _index_signature(self) -> str:
        return json.dumps([os.path.normcase(self.src), os.path.normcase(rel_to_volume(self.dst)),
                           self.patterns, sorted(self.excluded), self.mode])

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._scrub_stop.set()
        self._resume.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def discard(self) -> None:
        """Libera el índice de un análisis que no se va a copiar."""
        self._close_index(commit=False)

    def run(self, copy: bool = True) -> None:
        try:
            if self.phase != "ready":
                if not self.scan():
                    self._close_index(commit=False)
                    self.phase = "cancelled"
                    return
            if not copy:
                self.phase = "ready"
                return
            self.copy()
        except Exception as exc:  # noqa: BLE001 - se muestra en la UI
            self.fatal = f"{type(exc).__name__}: {exc}"
            self._close_index(commit=False)
            self.phase = "failed"

    # ── fase 1: análisis ─────────────────────────────────────────────────
    def _open_index(self) -> None:
        s = self.stats
        s.fast = False
        if not self.index_path:
            self.index_note = _("Lectura completa del destino")
            return
        last_full = 0.0
        if self.force_full:
            self.index_note = _("Revisión completa del destino (pedida a mano)")
        elif not os.path.exists(self.index_path):
            self.index_note = _("Revisión completa del destino (primera vez con este disco)")
        elif not os.path.isdir(lp(self.dst)):
            self.index_note = _("Revisión completa del destino (la carpeta no existe)")
        else:
            try:
                reader = IndexReader(self.index_path)
                last_full = float(reader.meta.get("last_full", 0))
                if reader.meta.get("signature") != self._index_signature():
                    self.index_note = _("Revisión completa del destino (cambió la configuración)")
                    reader.close()
                elif time.time() - last_full > FULL_CHECK_DAYS * 86400:
                    self.index_note = _("Revisión completa del destino (la última fue hace más de {days} "
                                        "días)").format(days=FULL_CHECK_DAYS)
                    reader.close()
                else:
                    self._reader = reader
                    s.fast = True
                    days = (time.time() - last_full) / 86400
                    self.index_note = _n("Análisis rápido con el índice de la última copia · revisión completa "
                                         "hace {n} día", "Análisis rápido con el índice de la última copia · "
                                         "revisión completa hace {n} días", round(days)).format(n=round(days))
            except (sqlite3.Error, ValueError):
                self.index_note = _("Revisión completa del destino (índice dañado)")
        self._last_full = last_full if s.fast else time.time()
        try:
            self._writer = IndexWriter(self.index_path)
        except (OSError, sqlite3.Error) as exc:
            self._writer = None
            self._error(self.index_path, exc)

    def _close_index(self, commit: bool) -> None:
        if self._reader:
            self._reader.close()
            self._reader = None
        if self._writer and not commit:
            self._writer.abort()
            self._writer = None

    def scan(self) -> bool:
        s = self.stats
        self.phase = "scanning"
        s.scan_start = time.perf_counter()
        # lo que cambie en el origen a partir de aquí ya no entra en esta copia (recordatorios)
        self.scan_started_at = datetime.now()
        self._open_index()
        stack = [""]
        while stack:
            if self._cancel.is_set():
                return False
            self._wait_if_paused()
            rel = stack.pop()
            sdir = os.path.join(self.src, rel) if rel else self.src
            ddir = os.path.join(self.dst, rel) if rel else self.dst
            s.current_dir = sdir
            dest = self._dest_state(rel, ddir)
            if dest is None:
                self.dirs.append(ddir)
                dfiles, ddirs = {}, {}
            else:
                dfiles, ddirs = dest
            self._seen_dirs.append(rel)
            try:
                with os.scandir(lp(sdir)) as it:
                    entries = list(it)
            except OSError as exc:
                self._error(sdir, exc)
                continue
            subdirs = []
            for entry in entries:
                name = entry.name
                key = os.path.normcase(name)
                try:
                    if is_link(entry):
                        s.links_skipped += 1
                        ddirs.pop(key, None)
                        dfiles.pop(key, None)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        ddirs.pop(key, None)
                        child = os.path.join(rel, name) if rel else name
                        rule = match_rule(name, self.patterns)
                        if not rel and name.lower() in ROOT_SKIP:
                            # papelera, puntos de restauración, índices de Spotlight…: no son datos tuyos
                            self.pruned.append((child, _("carpeta del sistema")))
                        elif rule:
                            s.excluded_rule += 1
                            self.pruned.append((child, _("regla «{rule}»").format(rule=rule)))
                        elif norm_rel(child) in self.excluded:
                            s.excluded_manual += 1
                            self.pruned.append((child, _("excluida a mano")))
                        else:
                            subdirs.append(child)
                    elif entry.is_file(follow_symlinks=False):
                        if name.endswith(TMP_SUFFIX):
                            continue
                        st = entry.stat(follow_symlinks=False)
                        size, mtime = st.st_size, st.st_mtime_ns
                        s.files_found += 1
                        s.bytes_found += size
                        have = dfiles.pop(key, None)
                        if have is not None and self._same(size, mtime, have):
                            s.skip_files += 1
                            s.skip_bytes += size
                            if self._writer:
                                self._writer.add_file(rel, have[0], have[1], have[2])
                        else:
                            kind = "c" if have else "n"
                            self.copies.append((os.path.join(sdir, name), size, mtime, kind, have))
                            if have:
                                s.changed_files += 1
                                s.changed_bytes += size
                            else:
                                s.new_files += 1
                                s.new_bytes += size
                except OSError as exc:
                    self._error(os.path.join(sdir, name), exc)
            if dest is not None:
                self._dest_leftovers(rel, ddir, dfiles, ddirs)
            subdirs.sort(key=str.lower, reverse=True)
            stack.extend(subdirs)
            s.dirs_scanned += 1
        if self.mode == MODE_MIRROR:
            self._detect_moves()
        s.plan_files = len(self.copies)
        s.plan_bytes = sum(c[1] for c in self.copies)
        s.dirs_to_create = len(self.dirs)
        s.orphan_dirs = len(self.orphan_dirs)
        s.junk_files = len(self.junk)
        s.current_dir = ""
        if self._reader:
            self._reader.close()
            self._reader = None
        s.scan_end = time.perf_counter()
        return True

    def _same(self, size: int, mtime: int, have: tuple) -> bool:
        _key, dsize, dmtime = have
        if dsize != size:
            return False
        diff = abs(dmtime - mtime)
        if diff <= self.tolerance_ns:
            return True
        # FAT guarda la hora local: tras un cambio de horario la fecha se desplaza 1 h exacta
        return self.fat and abs(diff - 3600 * 10**9) <= self.tolerance_ns

    def _dest_state(self, rel: str, ddir: str):
        """(archivos, subcarpetas) del destino en esta carpeta, o None si no existe."""
        if self._reader:
            if not self._reader.has_dir(rel):
                return None
            return self._reader.files(rel), self._reader.subdirs(rel)
        try:
            with os.scandir(lp(ddir)) as it:
                files, dirs = {}, {}
                for e in it:
                    try:
                        if is_link(e):
                            continue
                        if e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            files[os.path.normcase(e.name)] = (e.name, st.st_size, st.st_mtime_ns)
                        elif e.is_dir(follow_symlinks=False):
                            dirs[os.path.normcase(e.name)] = e.name
                    except OSError:
                        pass
                return files, dirs
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            self._error(ddir, exc)
            return {}, {}

    def _dest_leftovers(self, rel: str, ddir: str, dfiles: dict, ddirs: dict) -> None:
        """Lo que hay en el destino y ya no está en el origen."""
        s = self.stats
        for dname, dsize, dmtime in dfiles.values():
            path = os.path.join(ddir, dname)
            if dname.endswith(TMP_SUFFIX):
                self.junk.append(path)
            elif self.mode == MODE_MIRROR:
                self.deletes.append((path, dsize, dmtime))
                s.del_files += 1
                s.del_bytes += dsize
        if self.mode != MODE_MIRROR:
            return
        for dname in ddirs.values():
            child = os.path.join(rel, dname) if rel else dname
            if not rel and dname.lower() in ROOT_SKIP:
                continue
            if match_rule(dname, self.patterns) or norm_rel(child) in self.excluded:
                continue  # lo excluido no se toca en ningún lado
            self._orphan_tree(child)

    def _orphan_tree(self, rel: str) -> None:
        """Recoge una carpeta del destino que ya no existe en el origen."""
        s = self.stats
        stack = [rel]
        found_dirs = []
        while stack:
            cur = stack.pop()
            ddir = os.path.join(self.dst, cur)
            state = self._dest_state(cur, ddir)
            if state is None:
                continue
            files, dirs = state
            found_dirs.append(ddir)
            for dname, dsize, dmtime in files.values():
                path = os.path.join(ddir, dname)
                if dname.endswith(TMP_SUFFIX):
                    self.junk.append(path)
                else:
                    self.deletes.append((path, dsize, dmtime))
                    s.del_files += 1
                    s.del_bytes += dsize
            for dname in dirs.values():
                if not match_rule(dname, self.patterns):
                    stack.append(os.path.join(cur, dname))
        # las más profundas primero, para poder quitarlas cuando se queden vacías
        self.orphan_dirs.extend(sorted(found_dirs, key=lambda p: p.count(os.sep), reverse=True))

    def _detect_moves(self) -> None:
        """Empareja archivos desaparecidos del destino con archivos nuevos idénticos."""
        if not self.deletes or not any(c[3] == "n" for c in self.copies):
            return
        pairs: dict[int, int] = {}  # índice en copies -> índice en deletes
        used: set[int] = set()

        def match(keyfn, min_size):
            dmap: dict = {}
            for j, (path, size, mtime) in enumerate(self.deletes):
                if j not in used and size >= min_size:
                    dmap.setdefault(keyfn(os.path.basename(path), size, mtime), []).append(j)
            cmap: dict = {}
            for i, (src, size, mtime, kind, _have) in enumerate(self.copies):
                if kind == "n" and i not in pairs and size >= min_size:
                    cmap.setdefault(keyfn(os.path.basename(src), size, mtime), []).append(i)
            # solo parejas sin ambigüedad: un único candidato a cada lado
            for key, js in dmap.items():
                cs = cmap.get(key)
                if len(js) == 1 and cs is not None and len(cs) == 1:
                    pairs[cs[0]] = js[0]
                    used.add(js[0])

        match(lambda n, s, m: (os.path.normcase(n), s, m), 1)   # carpeta movida o renombrada
        match(lambda n, s, m: (s, m), MOVE_MIN_SIZE)            # archivo renombrado
        if not pairs:
            return
        s = self.stats
        for i, j in pairs.items():
            item, old = self.copies[i], self.deletes[j]
            self.moves.append((old[0], item))
            s.moved_files += 1
            s.moved_bytes += item[1]
            s.new_files -= 1
            s.new_bytes -= item[1]
            s.del_files -= 1
            s.del_bytes -= old[1]
        self.copies = [c for i, c in enumerate(self.copies) if i not in pairs]
        self.deletes = [d for j, d in enumerate(self.deletes) if j not in used]

    # ── fase 2: ejecución ────────────────────────────────────────────────
    def _dst_for(self, src: str) -> str:
        return os.path.join(self.dst, src[len(self.src):].lstrip("\\/"))

    def _version_path(self, dst_path: str) -> str:
        return os.path.join(self.version_root, dst_path[len(self.dst):].lstrip("\\/"))

    def copy(self) -> None:
        s = self.stats
        self.phase = "copying"
        s.copy_start = time.perf_counter()
        self._started_at = datetime.now()
        root = self.meta_root
        try:
            self.disk_id = ensure_disk_id(root)
        except OSError as exc:
            self.disk_id = ""
            self._error(os.path.join(root, META_DIR), exc)
        self.version_root = os.path.join(root, META_DIR, "versiones", self._started_at.strftime(STAMP_FMT),
                                         rel_to_volume(self.dst))
        self._move_ok = bytearray(len(self.moves))

        s.step = "limpieza"
        for path in self.junk:
            _remove_quiet(lp(path))

        s.step = "carpetas"
        for d in self.dirs:
            if self._cancel.is_set():
                break
            while True:
                try:
                    os.makedirs(lp(d), exist_ok=True)
                    s.dirs_created += 1
                except OSError as exc:
                    if self._await_disk():
                        continue
                    if not self._cancel.is_set():
                        self._error(d, exc)
                break

        s.step = "moviendo"
        for k, (old, item) in enumerate(self.moves):
            if self._cancel.is_set():
                break
            self._wait_if_paused()
            new = self._dst_for(item[0])
            try:
                try:
                    os.makedirs(lp(os.path.dirname(new)), exist_ok=True)
                    os.replace(lp(old), lp(new))
                except OSError:
                    if not self._await_disk():
                        raise
                    os.makedirs(lp(os.path.dirname(new)), exist_ok=True)
                    os.replace(lp(old), lp(new))
                self._move_ok[k] = 1
                s.moves_done += 1
            except OSError:
                # si no se puede mover, se copia el nuevo y el viejo pasa a versiones
                self.copies.append(item)
                self.deletes.append((old, item[1], item[2]))
                s.moved_files -= 1
                s.moved_bytes -= item[1]
                s.plan_files += 1
                s.plan_bytes += item[1]
                s.new_files += 1
                s.new_bytes += item[1]
                s.del_files += 1
                s.del_bytes += item[1]
        self._copy_ok = bytearray(len(self.copies))
        self._copy_hash: list[bytes | None] = [None] * len(self.copies)
        self._delete_ok = bytearray(len(self.deletes))

        if not self._cancel.is_set():
            s.step = "copiando"
            self._next = 0
            self._pool(self._copy_worker)

        if self.mode == MODE_MIRROR and not self._cancel.is_set():
            s.step = "versionando"
            self._next = 0
            self._pool(self._delete_worker)
            for d in self.orphan_dirs:
                try:
                    os.rmdir(lp(d))
                    self._removed_dirs.add(d)
                    s.dirs_removed += 1
                except OSError:
                    pass

        if not self._cancel.is_set() and self.keep_days > 0:
            s.step = "purgando"
            self._purge_versions(root)

        s.step = "índice"
        self._commit_index()

        # cancelar durante la revisión de integridad no deshace una sincronización ya completa
        sync_cancelled = self._cancel.is_set()
        if not sync_cancelled and self.scrub_seconds:
            s.step = "verificando"
            self._scrub()
        s.copy_end = time.perf_counter()
        self.phase = "cancelled" if sync_cancelled else "done"
        self._write_history(root)

    def _pool(self, target) -> None:
        workers = [threading.Thread(target=target, name=f"trabajo-{i + 1}", daemon=True)
                   for i in range(self.threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

    def _take(self, total: int) -> int | None:
        with self._lock:
            i = self._next
            if i >= total:
                return None
            self._next = i + 1
            return i

    def _wait_if_paused(self) -> None:
        while not self._resume.wait(0.2):
            if self._cancel.is_set():
                return

    def _dst_online(self) -> bool:
        return volume_present(self.dst) and os.path.isdir(lp(self.dst))

    def _await_disk(self) -> bool:
        """Tras un error: si es que el disco de copia ha desaparecido, espera a que vuelva.

        Pasa al suspender el equipo, al moverse el cable o cuando el USB se reinicia. En vez de
        dar por fallidos todos los archivos que quedan, los hilos esperan y repiten el archivo.
        Devuelve True si el disco ha vuelto; False si no se había ido o se cancela.
        """
        if self._dst_online():
            return False
        s = self.stats
        with self._lock:
            self._disk_waiters += 1
            if self._disk_waiters == 1:
                s.disk_lost_at = time.perf_counter()
                s.disk_waits += 1
        try:
            while not self._cancel.is_set():
                if self._dst_online():
                    self._cancel.wait(DISK_SETTLE)  # que termine de montarse
                    return not self._cancel.is_set()
                self._cancel.wait(1.0)
            return False
        finally:
            with self._lock:
                self._disk_waiters -= 1
                if not self._disk_waiters and s.disk_lost_at:
                    s.disk_wait_s += time.perf_counter() - s.disk_lost_at
                    s.disk_lost_at = 0.0

    def _copy_worker(self) -> None:
        tid = threading.get_ident()
        slot = [None, 0, 0]  # [ruta, tamaño, transferido]
        stats, lock = self.stats, self._lock

        def on_progress(_total, done, *_rest):
            delta = done - slot[2]
            if delta:
                slot[2] = done
                with lock:
                    stats.bytes_done += delta
            while not self._resume.is_set() and not self._cancel.is_set():
                self._resume.wait(0.2)
            return PROGRESS_CANCEL if self._cancel.is_set() else PROGRESS_CONTINUE

        callback = _PROGRESS(on_progress) if IS_WIN else None
        buf = None if IS_WIN else bytearray(CHUNK)
        rb = ReadBuffer() if self.verify else None
        try:
            while True:
                self._wait_if_paused()
                if self._cancel.is_set():
                    break
                i = self._take(len(self.copies))
                if i is None:
                    break
                src, size, _mtime, kind, _have = self.copies[i]
                dst = self._dst_for(src)
                slot[0], slot[1], slot[2] = src, size, 0
                self.active[tid] = slot
                big = size >= BIG_FILE
                if big:
                    self._big.acquire()
                cancelled = False
                try:
                    while True:
                        try:
                            digest = self._copy_file(src, dst, kind, callback, buf, slot, rb)
                        except _Cancelled:
                            with lock:
                                stats.bytes_done -= slot[2]
                            cancelled = True
                        except OSError as exc:
                            with lock:
                                stats.bytes_done -= slot[2]
                            slot[2] = 0
                            if self._await_disk():
                                continue  # el disco ha vuelto: se repite este archivo
                            if self._cancel.is_set():
                                cancelled = True  # se canceló esperando al disco: queda pendiente
                            else:
                                with lock:
                                    stats.files_failed += 1
                                    stats.bytes_failed += size
                                self._error(src, exc)
                        else:
                            self._copy_ok[i] = 1
                            self._copy_hash[i] = digest
                            with lock:
                                # cuadra con el tamaño del plan aunque el archivo traiga flujos alternativos
                                stats.bytes_done += size - slot[2]
                                stats.files_done += 1
                                if kind == "c":
                                    stats.changed_done += 1
                                    stats.changed_done_bytes += size
                                else:
                                    stats.new_done += 1
                                    stats.new_done_bytes += size
                        break
                finally:
                    if big:
                        self._big.release()
                    self.active.pop(tid, None)
                if cancelled:
                    break
        finally:
            if rb:
                rb.free()

    def _delete_worker(self) -> None:
        """En modo espejo, lo borrado del origen pasa a versiones (no se elimina)."""
        while True:
            self._wait_if_paused()
            if self._cancel.is_set():
                break
            i = self._take(len(self.deletes))
            if i is None:
                break
            path = self.deletes[i][0]
            while True:
                try:
                    self._archive(path)
                except OSError as exc:
                    # sin el disco, «no existe» no quiere decir que ya esté borrado
                    if self._await_disk():
                        continue
                    if self._cancel.is_set():
                        pass  # se canceló esperando al disco: queda pendiente
                    elif isinstance(exc, FileNotFoundError):
                        self._delete_ok[i] = 1
                        with self._lock:
                            self.stats.deletes_done += 1
                    else:
                        with self._lock:
                            self.stats.other_failed += 1
                        self._error(path, exc)
                else:
                    self._delete_ok[i] = 1
                    with self._lock:
                        self.stats.deletes_done += 1
                break

    def _archive(self, dst_path: str) -> None:
        target = self._version_path(dst_path)
        os.makedirs(lp(os.path.dirname(target)), exist_ok=True)
        os.replace(lp(dst_path), lp(target))
        with self._lock:
            self.stats.versions_saved += 1

    def _copy_file(self, src: str, dst: str, kind: str, callback, buf, slot, rb: ReadBuffer | None) -> bytes | None:
        """Copia a un temporal, lo comprueba releyendo el disco y lo pone en su sitio.

        Devuelve el SHA-256 comprobado, o None si la verificación está desactivada.
        """
        tmp = dst + TMP_SUFFIX
        ltmp, ldst = lp(tmp), lp(dst)
        digest = None
        for attempt in (1, 2):
            if IS_WIN:
                self._copy_win(lp(src), ltmp, callback, slot)
            else:
                self._copy_py(lp(src), ltmp, buf, slot)
            if rb is None:
                break
            try:
                expected = file_digest(src, rb, stop=self._cancel)
                written = file_digest(tmp, rb, uncached=True, stop=self._cancel)
            except BaseException:
                _remove_quiet(ltmp)
                raise
            if expected == written:
                digest = expected
                break
            _remove_quiet(ltmp)
            with self._lock:
                self.stats.bytes_done -= slot[2]
            slot[2] = 0
            if attempt == 2:
                raise OSError(0, _("la copia no coincide con el original tras dos intentos "
                                   "(¿el archivo cambia mientras se copia o el disco da errores?)"))
            with self._lock:
                self.stats.verify_retries += 1
        if kind == "c":
            try:
                self._archive(dst)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._error(dst, exc, _("no se pudo guardar la versión anterior; se sobrescribe"))
                _make_writable(ldst)
        try:
            os.replace(ltmp, ldst)
        except PermissionError:
            _make_writable(ldst)
            try:
                os.replace(ltmp, ldst)
            except OSError:
                _remove_quiet(ltmp)
                raise
        except OSError:
            _remove_quiet(ltmp)
            raise
        if digest is not None:
            # se cuenta al quedar en su sitio: si el disco se va antes, el archivo se repite
            with self._lock:
                self.stats.verified_copies += 1
        return digest

    def _copy_win(self, lsrc: str, ltmp: str, callback, slot) -> None:
        for attempt in (1, 2):
            if _CopyFileExW(lsrc, ltmp, callback, None, None, 0):
                return
            err = ctypes.get_last_error()
            if err == ERROR_REQUEST_ABORTED or self._cancel.is_set():
                _remove_quiet(ltmp)
                raise _Cancelled
            if attempt == 1 and err in (ERROR_ACCESS_DENIED, ERROR_PATH_NOT_FOUND):
                # temporal de solo lectura de una ejecución interrumpida, o carpeta
                # que el índice daba por existente y alguien borró
                _remove_quiet(ltmp)
                if err == ERROR_PATH_NOT_FOUND:
                    try:
                        os.makedirs(os.path.dirname(ltmp), exist_ok=True)
                    except OSError:
                        pass
                with self._lock:
                    self.stats.bytes_done -= slot[2]
                slot[2] = 0
                continue
            _remove_quiet(ltmp)
            raise ctypes.WinError(err)

    def _copy_py(self, lsrc: str, ltmp: str, buf: bytearray, slot) -> None:
        view = memoryview(buf)
        try:
            with open(lsrc, "rb") as fi, open(ltmp, "wb") as fo:
                while True:
                    self._wait_if_paused()
                    if self._cancel.is_set():
                        raise _Cancelled
                    n = fi.readinto(view)
                    if not n:
                        break
                    fo.write(view[:n])
                    slot[2] += n
                    with self._lock:
                        self.stats.bytes_done += n
            shutil.copystat(lsrc, ltmp)
        except BaseException:
            _remove_quiet(ltmp)
            raise

    def _purge_versions(self, root: str) -> None:
        base = os.path.join(root, META_DIR, "versiones")
        limit = datetime.now() - timedelta(days=self.keep_days)
        try:
            names = os.listdir(base)
        except OSError:
            return
        for name in names:
            try:
                when = datetime.strptime(name, STAMP_FMT)
            except ValueError:
                continue
            if when < limit:
                try:
                    _rmtree_force(os.path.join(base, name))
                    self.stats.versions_purged += 1
                except OSError as exc:
                    self._error(os.path.join(base, name), exc)

    def _commit_index(self) -> None:
        w = self._writer
        if not w:
            return
        try:
            cut = len(self.src)
            now = time.time()
            for i, (src, size, mtime, _kind, have) in enumerate(self.copies):
                rel_dir, name = _split_rel(src[cut:].lstrip("\\/"))
                if self._copy_ok[i]:
                    digest = self._copy_hash[i]
                    w.add_file(rel_dir, name, size, mtime, digest, now if digest else None)
                elif have:
                    w.add_file(rel_dir, have[0], have[1], have[2])
            for k, (old, item) in enumerate(self.moves):
                if self._move_ok[k]:
                    rel_dir, name = _split_rel(item[0][cut:].lstrip("\\/"))
                    w.add_file(rel_dir, name, item[1], item[2])
                # si falló, el archivo ya figura entre las copias y los borrados
            dcut = len(self.dst)
            for j, (path, size, mtime) in enumerate(self.deletes):
                if not self._delete_ok[j]:
                    rel_dir, name = _split_rel(path[dcut:].lstrip("\\/"))
                    w.add_file(rel_dir, name, size, mtime)
            dirs = list(self._seen_dirs)
            dirs += [d[dcut:].lstrip("\\/") for d in self.orphan_dirs if d not in self._removed_dirs]
            w.commit(dirs, {"signature": self._index_signature(), "last_full": self._last_full,
                            "updated": time.time()})
        except (OSError, sqlite3.Error) as exc:
            w.abort()
            self._error(self.index_path or "", exc)
        finally:
            self._writer = None

    # ── revisión de integridad ───────────────────────────────────────────
    def _scrub(self) -> None:
        """Relee una tanda de archivos del destino y los compara con el original.

        Orden: primero los que nunca se han comprobado, luego los más antiguos,
        así con el tiempo todo el disco queda revisado. Usa el hash guardado
        para decidir qué lado se ha estropeado.
        """
        s = self.stats
        if not self.index_path or not os.path.exists(self.index_path):
            return
        # todo el disco a mano: sin plazo; si no, solo lo nunca comprobado o comprobado hace tiempo
        since = 0.0 if self.scrub_seconds < 0 else time.time() - SCRUB_RECHECK_DAYS * 86400
        try:
            con = sqlite3.connect(self.index_path, check_same_thread=False)
            s.scrub_total, s.scrub_covered = con.execute("SELECT count(*), count(verified) FROM files").fetchone()
            s.scrub_due = con.execute("SELECT count(*) FROM files WHERE verified IS NULL OR verified < ?",
                                      (since or time.time() + 1,)).fetchone()[0]
            if not s.scrub_due:
                con.close()
                return  # todo comprobado hace poco: nada que revisar
            cur = con.execute(
                "SELECT f.dir, d.path, f.name, f.size, f.mtime, f.hash FROM files f JOIN dirs d ON d.key = f.dir "
                "WHERE f.verified IS NULL OR f.verified < ? ORDER BY f.verified IS NOT NULL, f.verified",
                (since or time.time() + 1,))
        except sqlite3.Error as exc:
            self._error(self.index_path, exc)
            return
        s.scrub_start = time.perf_counter()
        timer = None
        if self.scrub_seconds > 0:
            # el límite se respeta también a mitad de un archivo grande
            timer = threading.Timer(self.scrub_seconds, self._scrub_stop.set)
            timer.daemon = True
            timer.start()
        updates: list[tuple] = []
        lock = threading.Lock()

        def worker():
            rb = ReadBuffer()
            slot = [None, 0, 0]
            noop = _PROGRESS(lambda *_a: PROGRESS_CANCEL if self._cancel.is_set() else PROGRESS_CONTINUE) \
                if IS_WIN else None
            buf = None if IS_WIN else bytearray(CHUNK)
            tid = threading.get_ident()
            try:
                while True:
                    self._wait_if_paused()
                    if self._cancel.is_set() or self._scrub_stop.is_set():
                        return
                    with lock:
                        row = cur.fetchone()
                    if row is None:
                        return
                    key, rel_dir, name, size, mtime, stored = row
                    result = self._scrub_one(rel_dir, name, size, mtime, stored, rb, noop, buf, slot, tid)
                    if result is not None:
                        with lock:
                            updates.append((result, time.time(), key, name))
            finally:
                rb.free()

        threads = [threading.Thread(target=worker, name=f"revision-{i + 1}", daemon=True)
                   for i in range(min(self.threads, SCRUB_THREADS))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if timer:
            timer.cancel()
        try:
            cur.close()
            con.executemany("UPDATE files SET hash=?, verified=? WHERE dir=? AND name=?", updates)
            con.commit()
            s.scrub_covered = con.execute("SELECT count(verified) FROM files").fetchone()[0]
        except sqlite3.Error as exc:
            self._error(self.index_path, exc)
        finally:
            con.close()

    def _scrub_one(self, rel_dir, name, size, mtime, stored, rb, callback, buf, slot, tid) -> bytes | None:
        """Comprueba un archivo; devuelve el hash a guardar, o None si no se pudo comprobar."""
        src = os.path.join(self.src, rel_dir, name)
        dst = os.path.join(self.dst, rel_dir, name)
        try:
            st = os.stat(lp(src))
        except OSError:
            return None  # ya no está en el origen (o modo «solo añadir»)
        if not self._same(st.st_size, st.st_mtime_ns, (name, size, mtime)):
            return None  # cambió después del análisis; lo recogerá la próxima sincronización
        self.active[tid] = [dst, size, 0]
        try:
            try:
                on_disk = file_digest(dst, rb, uncached=True, stop=self._scrub_stop)
            except FileNotFoundError:
                if not self._dst_online():
                    raise  # no falta el archivo, falta el disco: abajo se espera y se repite
                digest = self._copy_file(src, dst, "n", callback, buf, slot, rb)
                with self._lock:
                    self.stats.scrub_repaired += 1
                self._error(dst, OSError(0, _("faltaba en el destino (¿borrado a mano?); copiado de nuevo "
                                              "desde el original")))
                return digest or file_digest(src, rb, stop=self._cancel)
            original = file_digest(src, rb, stop=self._scrub_stop)
            with self._lock:
                self.stats.scrub_files += 1
                self.stats.scrub_bytes += size
            if on_disk == original:
                return on_disk
            if stored is not None and on_disk == stored:
                # el destino está como se copió; el que ha cambiado es el original
                with self._lock:
                    self.stats.scrub_alerts += 1
                self._error(src, OSError(0, _("el original ha cambiado sin cambiar su fecha ni su tamaño "
                                              "(¿daño en el disco de origen?). La copia buena sigue en el "
                                              "destino y no se ha tocado")))
                return stored
            # el destino no coincide con el original: se repara (lo dañado pasa a versiones)
            digest = self._copy_file(src, dst, "c", callback, buf, slot, rb)
            with self._lock:
                self.stats.scrub_repaired += 1
            self._error(dst, OSError(0, _("contenido dañado en el destino; reparado desde el original "
                                          "(la versión dañada está en versiones)")))
            return digest or original
        except _Cancelled:
            return None
        except OSError as exc:
            if self._await_disk():
                self.active.pop(tid, None)
                return self._scrub_one(rel_dir, name, size, mtime, stored, rb, callback, buf, slot, tid)
            if not self._cancel.is_set():
                self._error(dst, exc, _("revisión de integridad"))
            return None
        finally:
            self.active.pop(tid, None)

    def _write_history(self, root: str) -> None:
        s = self.stats
        self.summary = {
            "inicio": self._started_at.isoformat(timespec="seconds"),
            "fin": datetime.now().isoformat(timespec="seconds"),
            "resultado": self.phase,
            "origen": self.src,
            "destino": self.dst,
            "modo": self.mode,
            "analisis_s": round(s.scan_end - s.scan_start, 3),
            "copia_s": round(s.copy_end - s.copy_start, 3),
            "rapido": s.fast,
            "nuevos": s.new_files, "modificados": s.changed_files, "eliminados": s.del_files,
            "movidos": s.moved_files, "copiados": s.files_done, "bytes": s.bytes_done,
            "versiones": s.versions_saved, "errores": s.errors,
            "verificados": s.verified_copies,
            "integridad": {"revisados": s.scrub_files, "reparados": s.scrub_repaired, "alertas": s.scrub_alerts,
                           "cubiertos": s.scrub_covered, "total": s.scrub_total},
        }
        append_history(root, self.summary)

    # ── utilidades ───────────────────────────────────────────────────────
    def _error(self, path: str, exc: BaseException, context: str = "") -> None:
        msg = getattr(exc, "strerror", None) or str(exc)
        winerr = getattr(exc, "winerror", None)
        if winerr:
            msg = _("{msg} (código {code})").format(msg=msg, code=winerr)
        if context:
            msg = f"{context}: {msg}"
        with self._lock:
            self.errors.append((path, msg))
            self.stats.errors += 1
