"""Copias de servidores Plesk con su herramienta oficial, a través de la extensión «DEV Backup Studio».

1. En Plesk (Extensiones → DEV Backup Studio) se pulsa «Conectar un ordenador» y aparece un código de
   6 cifras: sirve una vez, caduca a los 10 minutos y se anula tras 5 fallos.
2. pair(): la app canjea el código por un token propio de este ordenador. El token y la contraseña de
   las copias se guardan en Secrets (Windows: cifrados con DPAPI; macOS y Linux: en un archivo que
   solo puede leer tu usuario).
3. BackupRun: la extensión ejecuta «plesk bin pleskbackup server», la copia completa oficial de Plesk
   (ajustes del servidor, suscripciones, webs, bases de datos, correo, DNS…), y deja un único archivo.
   La app lo descarga (si se corta, sigue donde iba), comprueba su SHA-256 y lo guarda en
   PLESK/<servidor>/ en la raíz del disco de copia. Luego se borra del servidor.
4. RestoreRun: sube el archivo por partes (también reanudable); la extensión comprueba su SHA-256 y
   lo restaura con «plesk bin pleskrestore» (todo el servidor) o lo deja en el Gestor de copias de
   seguridad de Plesk para elegir allí qué restaurar.

HTTPS: si el certificado del panel no es de una autoridad conocida (el autofirmado de Plesk), se
enseña su huella SHA-256 —la misma que muestra la extensión junto al código— y, una vez aceptada,
queda fijada: cualquier otro certificado se rechaza.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets as _secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

EXT_ID = "dev-backup-studio"
API_PATH = f"/modules/{EXT_ID}/public/api.php"
PORT = 8443
PROTOCOL = 1                       # versión del protocolo entre la app y la extensión
PLESK_DIR = "PLESK"                # carpeta en la raíz del disco de copia
BACKUP_EXT = ".tar"
META_EXT = ".json"
TMP_SUFFIX = ".bkpart"             # el mismo que usa engine para lo que está a medias
STAMP_FMT = "%Y-%m-%d %H%M%S"
UPLOAD_CHUNK = 8 * 1024 * 1024     # muy por debajo de los límites de PHP y nginx del panel
READ_CHUNK = 1024 * 1024
SPACE_RESERVE = 256 * 1024 * 1024  # lo que se deja libre de más en el disco de copia
SERVER_RESERVE = 2 * 1024 ** 3     # y en el servidor, además del doble de la copia anterior
POLL_S = 2.0
RETRIES = 6                        # cortes de red seguidos antes de dar la descarga por fallida
RESTORE_DOWN_S = 45 * 60           # mientras se restaura, el panel puede no responder un buen rato
USER_AGENT = "DEV-Backup-Studio"
ASSETS = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "plesk")
SCHEDULES = ("manual", "daily", "weekly")
SCHEDULE_DAYS = {"manual": 0, "daily": 1, "weekly": 7}
KEEP_OPTIONS = (3, 7, 14, 30)
KEEP_DEFAULT = 7


class PleskError(Exception):
    """Error para enseñar a la persona; quien lo muestra traduce «kind».

    kind: code | unreachable | cert | auth | noext | old | busy | running | server | space | serverspace |
          serverfull |
          signature | verify | missing | cancelled
    """

    def __init__(self, kind: str, detail: str = "", fingerprint: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.fingerprint = fingerprint   # «cert»: la huella del certificado que presentó el servidor


# ── direcciones y certificados ──────────────────────────────────────────────

def normalize_url(text: str) -> str:
    """«plesk.ejemplo.com», «https://plesk.ejemplo.com:8443/admin» → «https://plesk.ejemplo.com:8443».

    Sin puerto se usa el del panel (8443). http:// solo se admite hacia este mismo equipo (pruebas)."""
    text = (text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        u = urllib.parse.urlsplit(text)
        port = u.port
    except ValueError:
        return ""
    scheme = u.scheme.lower()
    host = (u.hostname or "").strip(".").lower()
    if not host or scheme not in ("https", "http") or any(c in host for c in " /\\@"):
        return ""
    if scheme == "http" and not _loopback(host):
        scheme = "https"
    port = port or (PORT if scheme == "https" else 80)
    netloc = f"[{host}]" if ":" in host else host
    return f"{scheme}://{netloc}:{port}"


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def url_host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or url


def fmt_fingerprint(fp: str) -> str:
    """«ab12…» → «AB:12:…» (como la enseña la extensión)."""
    fp = fp.replace(":", "").upper()
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))


def _ca_context() -> ssl.SSLContext:
    # la app de macOS no trae los certificados del sistema: se usan los de certifi
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _pin_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # se comprueba la huella fijada justo después de conectar
    return ctx


def _connect(url: str, fingerprint: str, timeout: float) -> http.client.HTTPConnection:
    u = urllib.parse.urlsplit(url)
    host, port = u.hostname, u.port
    try:
        if u.scheme == "http":
            conn: http.client.HTTPConnection = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.connect()
            return conn
        ctx = _pin_context() if fingerprint else _ca_context()
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        conn.connect()
    except ssl.SSLCertVerificationError as exc:
        raise PleskError("cert", str(exc.verify_message or exc), peer_fingerprint(url)) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise PleskError("unreachable", str(exc)) from exc
    if fingerprint:
        der = conn.sock.getpeercert(binary_form=True) or b""
        got = hashlib.sha256(der).hexdigest()
        if got != fingerprint.replace(":", "").lower():
            conn.close()
            raise PleskError("cert", "fingerprint", got)
    return conn


def peer_fingerprint(url: str, timeout: float = 10.0) -> str:
    """Huella SHA-256 del certificado que presenta el servidor, sin comprobarlo."""
    u = urllib.parse.urlsplit(url)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=timeout) as raw:
            with _pin_context().wrap_socket(raw, server_hostname=u.hostname) as tls:
                return hashlib.sha256(tls.getpeercert(binary_form=True) or b"").hexdigest()
    except (OSError, ssl.SSLError):
        return ""


def check_certificate(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """(¿de una autoridad conocida?, huella). Sirve para decidir si hay que fijarlo al emparejar."""
    if url.startswith("http://"):
        return True, ""
    conn = None
    try:
        conn = _connect(url, "", timeout)
        return True, hashlib.sha256(conn.sock.getpeercert(binary_form=True) or b"").hexdigest()
    except PleskError as exc:
        if exc.kind == "cert":
            return False, exc.fingerprint
        raise
    finally:
        if conn is not None:
            conn.close()


# ── servidores conectados ───────────────────────────────────────────────────

@dataclass
class Server:
    id: str                      # el de la extensión para este ordenador
    url: str                     # https://host:8443
    name: str = ""               # nombre del servidor en Plesk (su hostname)
    fingerprint: str = ""        # certificado fijado ("" = se comprueba con las autoridades del sistema)
    plesk_version: str = ""
    os: str = ""
    ext_version: str = ""
    schedule: str = "weekly"     # manual | daily | weekly
    keep: int = KEEP_DEFAULT     # copias que se conservan en el disco
    last_ok: str = ""            # ISO de la última copia completa
    last_size: int = 0
    last_try: str = ""
    last_error: str = ""
    pending_job: str = ""        # copia hecha en el servidor que aún no se ha bajado entera
    pending_stamp: str = ""

    @property
    def label(self) -> str:
        return self.name or url_host(self.url)

    @property
    def folder_name(self) -> str:
        return safe_name(self.label)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Server | None":
        if not isinstance(data, dict) or not data.get("id") or not data.get("url"):
            return None
        names = set(cls.__dataclass_fields__)
        srv = cls(**{k: v for k, v in data.items() if k in names})
        if srv.schedule not in SCHEDULES:
            srv.schedule = "weekly"
        try:
            srv.keep = max(1, int(srv.keep))
        except (TypeError, ValueError):
            srv.keep = KEEP_DEFAULT
        return srv


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:80] or "plesk"


# ── secretos: token y contraseña de las copias ──────────────────────────────

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


def token_key(server_id: str) -> str:
    return f"plesk/{server_id}/token"


def password_key(server_id: str) -> str:
    return f"plesk/{server_id}/password"


def new_password() -> str:
    """Contraseña para cifrar los datos sensibles de las copias (Plesk la pide al restaurar)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "-".join("".join(_secrets.choice(alphabet) for _ in range(5)) for _ in range(5))


# ── la API de la extensión ──────────────────────────────────────────────────

class Client:
    def __init__(self, url: str, token: str = "", fingerprint: str = "", timeout: float = 30.0):
        self.url = url
        self.token = token
        self.fingerprint = fingerprint
        self.timeout = timeout

    def _path(self, action: str, params: dict | None = None) -> str:
        return API_PATH + "?" + urllib.parse.urlencode({"a": action, "v": PROTOCOL, **(params or {})})

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.token:
            h["X-DBS-Token"] = self.token
        h.update(extra or {})
        return h

    def open(self, method: str, action: str, params: dict | None = None, body=None,
             headers: dict | None = None, timeout: float | None = None):
        """Petición cruda: devuelve (conexión, respuesta). Quien llama cierra la conexión."""
        conn = _connect(self.url, self.fingerprint, timeout or self.timeout)
        try:
            conn.request(method, self._path(action, params), body=body, headers=self._headers(headers))
            return conn, conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise PleskError("unreachable", str(exc)) from exc

    def call(self, method: str, action: str, params: dict | None = None, data: dict | None = None,
             body: bytes | None = None, headers: dict | None = None, timeout: float | None = None) -> dict:
        extra = dict(headers or {})
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            extra["Content-Type"] = "application/json"
        conn, resp = self.open(method, action, params, body, extra, timeout)
        try:
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise PleskError("unreachable", str(exc)) from exc
        finally:
            conn.close()
        return _parse(resp.status, resp.getheader("Content-Type", ""), raw)

    # ── acciones ──
    def pair(self, code: str, device: str) -> dict:
        return self.call("POST", "pair", data={"code": code, "device": device[:64] or "PC"})

    def info(self) -> dict:
        return self.call("GET", "info")

    def start_backup(self, password: str, options: dict | None = None) -> dict:
        return self.call("POST", "backup", data={"password": password, **(options or {})})["job"]

    def job(self, job_id: str) -> dict:
        return self.call("GET", "job", {"id": job_id})["job"]

    def cancel(self, job_id: str) -> None:
        self.call("POST", "cancel", {"id": job_id}, data={})

    def release(self, job_id: str) -> None:
        self.call("POST", "release", {"id": job_id}, data={})

    def upload_start(self, name: str, size: int, sha256: str) -> dict:
        return self.call("POST", "upload_start", data={"name": name, "size": size, "sha256": sha256})["upload"]

    def upload_chunk(self, upload_id: str, offset: int, chunk: bytes) -> dict:
        return self.call("POST", "upload_chunk", {"id": upload_id, "offset": offset}, body=chunk,
                         headers={"Content-Type": "application/octet-stream"}, timeout=120)["upload"]

    def start_restore(self, upload_id: str, password: str, mode: str) -> dict:
        return self.call("POST", "restore", data={"upload": upload_id, "password": password, "mode": mode})["job"]

    def unpair(self) -> None:
        self.call("POST", "unpair", data={})


def _parse(status: int, ctype: str, raw: bytes) -> dict:
    data = None
    if "json" in ctype.lower():
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            data = None
    if isinstance(data, dict) and data.get("error"):
        kind = str(data["error"])
        detail = str(data.get("message") or "")
        if kind in ("code", "auth", "busy", "old", "serverspace", "running", "verify", "missing", "server"):
            raise PleskError(kind, detail)
        raise PleskError("server", detail or kind)
    if status in (401, 403):
        raise PleskError("auth")
    if status == 429:
        raise PleskError("busy")
    if status == 404 or 300 <= status < 400 or (status == 200 and not isinstance(data, dict)):
        # sin la extensión, Plesk responde con su página de inicio de sesión o un 404
        raise PleskError("noext", f"HTTP {status}")
    if status >= 400 or not isinstance(data, dict):
        raise PleskError("server", f"HTTP {status}")
    if int(data.get("protocol", PROTOCOL)) < PROTOCOL:
        raise PleskError("old", str(data.get("ext_version", "")))
    return data


def pair(url: str, code: str, device: str, fingerprint: str = "") -> tuple[Server, str]:
    """Canjea el código de la extensión. Devuelve el servidor y su token."""
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        raise PleskError("code")
    data = Client(url, fingerprint=fingerprint).pair(code, device)
    token = str(data.get("token") or "")
    if len(token) < 32:
        raise PleskError("server", "token")
    srv = Server(id=str(data.get("device") or uuid.uuid4().hex), url=url, fingerprint=fingerprint)
    apply_info(srv, data.get("server") or {})
    return srv, token


def apply_info(srv: Server, info: dict) -> None:
    srv.name = str(info.get("hostname") or srv.name or url_host(srv.url))
    srv.plesk_version = str(info.get("plesk_version") or srv.plesk_version)
    srv.os = str(info.get("os") or srv.os)
    srv.ext_version = str(info.get("ext_version") or srv.ext_version)


# ── el disco de copia ───────────────────────────────────────────────────────

@dataclass
class StoredBackup:
    path: str
    when: datetime
    size: int
    sha256: str = ""
    info: dict = field(default_factory=dict)

    @property
    def meta_path(self) -> str:
        return self.path[:-len(BACKUP_EXT)] + META_EXT


def plesk_root(volume_root: str) -> str:
    return os.path.join(volume_root, PLESK_DIR)


def list_servers_on_disk(root: str) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(root) if e.is_dir() and not e.name.startswith("."))
    except OSError:
        return []


def list_backups(folder: str) -> list[StoredBackup]:
    """Copias completas de un servidor, de la más reciente a la más antigua."""
    out = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for e in entries:
        if not e.name.endswith(BACKUP_EXT) or not e.is_file():
            continue
        try:
            when = datetime.strptime(e.name[:-len(BACKUP_EXT)], STAMP_FMT)
            size = e.stat().st_size
        except (ValueError, OSError):
            continue
        b = StoredBackup(e.path, when, size)
        try:
            with open(b.meta_path, encoding="utf-8") as fh:
                b.info = json.load(fh)
            b.sha256 = str(b.info.get("sha256") or "")
        except (OSError, ValueError):
            pass
        out.append(b)
    out.sort(key=lambda b: b.when, reverse=True)
    return out


def prune(folder: str, keep: int) -> list[StoredBackup]:
    """Deja las «keep» copias más recientes y quita las demás. Devuelve las quitadas."""
    gone = []
    for b in list_backups(folder)[max(1, keep):]:
        try:
            os.remove(b.path)
        except OSError:
            continue
        try:
            os.remove(b.meta_path)
        except OSError:
            pass
        gone.append(b)
    return gone


def delete_backup(b: StoredBackup) -> None:
    os.remove(b.path)
    try:
        os.remove(b.meta_path)
    except OSError:
        pass


def file_sha256(path: str, progress=None, stop: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        while True:
            if stop is not None and stop.is_set():
                raise PleskError("cancelled")
            block = fh.read(READ_CHUNK * 4)
            if not block:
                break
            h.update(block)
            done += len(block)
            if progress:
                progress(done)
    return h.hexdigest()


def ensure_folder(root: str) -> str:
    """Crea la carpeta PLESK con el icono de Plesk (Explorador, Finder, Nautilus y Dolphin)."""
    os.makedirs(root, exist_ok=True)
    try:
        if IS_WIN:
            _icon_windows(root)
        elif IS_MAC:
            _icon_mac(root)
        else:
            _icon_linux(root)
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


def _icon_windows(root: str) -> None:
    k32 = ctypes.windll.kernel32
    ico = os.path.join(root, "plesk.ico")
    ini = os.path.join(root, "desktop.ini")
    changed = _copy_if_changed(os.path.join(ASSETS, "plesk.ico"), ico)
    text = "[.ShellClassInfo]\r\nIconResource=plesk.ico,0\r\nIconFile=plesk.ico\r\nIconIndex=0\r\n" \
           "InfoTip=Plesk\r\n"
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


def _icon_mac(root: str) -> None:
    marker = os.path.join(root, "Icon\r")  # el icono propio de una carpeta vive en este archivo
    if os.path.exists(marker):
        return
    subprocess.run(["osascript", "-l", "JavaScript", "-e", _MAC_SET_ICON, os.path.join(ASSETS, "plesk.png"), root],
                   capture_output=True, timeout=20)


def _icon_linux(root: str) -> None:
    png = os.path.join(root, ".plesk.png")
    _copy_if_changed(os.path.join(ASSETS, "plesk.png"), png)
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


# ── operaciones: copia y restauración ───────────────────────────────────────

class Operation:
    """Una copia o restauración en marcha. Se ejecuta en un hilo; la interfaz solo lee sus campos."""

    kind = ""

    def __init__(self, server: Server, token: str):
        self.server = server
        self.client = Client(server.url, token, server.fingerprint)
        self.phase = "connect"   # connect | server | download | verify | upload | restore | done | failed | cancelled
        self.message = ""        # última línea del registro de Plesk
        self.prepared = 0        # bytes que Plesk lleva escritos en el servidor (copia o restauración)
        self.activity: list[str] = []   # lo último del registro propio de Plesk (/var/log/plesk/PMM)
        self.log: list[str] = []
        self.bytes_done = 0
        self.bytes_total = 0
        self.resumed_from = 0
        self.error: PleskError | None = None
        self.result: StoredBackup | None = None
        self.warning = ""        # terminó, pero hay algo que contar
        self.started = time.monotonic()
        self.phase_started = self.started
        self.wall = datetime.now()
        self.stop = threading.Event()
        self.on_change = None    # se llama desde el hilo al cambiar algo que hay que guardar

    @property
    def running(self) -> bool:
        return self.phase not in ("done", "failed", "cancelled")

    def cancel(self) -> None:
        self.stop.set()

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self.phase_started = time.monotonic()

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise PleskError("cancelled")

    def _sleep(self, seconds: float) -> None:
        if self.stop.wait(seconds):
            raise PleskError("cancelled")

    def _note(self, job: dict) -> None:
        lines = [str(x) for x in (job.get("log") or []) if str(x).strip()]
        if lines:
            self.log = lines[-200:]
        # lo que cuenta Plesk en su propio registro; si aún no hay nada, lo nuestro (sin la línea de
        # arranque «DEV Backup Studio: plesk bin …», que no dice nada a quien mira)
        activity = [str(x).strip() for x in (job.get("activity") or []) if str(x).strip()]
        if activity:
            self.activity = activity[-60:]
        ours = [x.strip() for x in lines if "DEV Backup Studio:" not in x]
        shown = activity or ours
        self.message = shown[-1][:300] if shown else ""
        try:
            self.prepared = max(0, int(job.get("prepared") or 0))
        except (TypeError, ValueError):
            pass

    def run(self) -> None:
        try:
            self._run()
            self._set_phase("done")
        except PleskError as exc:
            self.error = exc
            self._set_phase("cancelled" if exc.kind == "cancelled" else "failed")
        except Exception as exc:  # noqa: BLE001 - se enseña tal cual y se puede repetir
            self.error = PleskError("server", f"{type(exc).__name__}: {exc}")
            self._set_phase("failed")

    def _run(self) -> None:
        raise NotImplementedError

    def _wait_job(self, job_id: str, tolerate_down: float = 0.0) -> dict:
        """Espera a que termine un trabajo del servidor. Si se cancela, lo cancela también allí."""
        down_since = None
        fails = 0
        while True:
            if self.stop.is_set():
                try:
                    self.client.cancel(job_id)
                except PleskError:
                    pass
                raise PleskError("cancelled")
            try:
                job = self.client.job(job_id)
                down_since = None
                fails = 0
            except PleskError as exc:
                if exc.kind != "unreachable":
                    raise
                fails += 1
                down_since = down_since or time.monotonic()
                limit = tolerate_down or POLL_S * 30
                if time.monotonic() - down_since > limit:
                    raise
                self.message = exc.detail[:300]
                self.stop.wait(min(30.0, POLL_S * fails))  # si se cancela, la vuelta siguiente avisa al servidor
                continue
            self._note(job)
            state = job.get("state")
            if state == "done":
                return job
            if state in ("failed", "canceled", "cancelled"):
                error = str(job.get("error") or self.message or state)
                # pmm-ras rechaza en el Gestor de copias las copias hechas en otro servidor
                kind = "signature" if error.startswith("signature") else "server"
                if "No space left on device" in error or "Failed to write volume" in error:
                    kind = "serverfull"
                raise PleskError(kind, error)
            self.stop.wait(POLL_S)


class BackupRun(Operation):
    """Copia completa del servidor con «pleskbackup server» → archivo verificado en PLESK/<servidor>/."""

    kind = "backup"

    def __init__(self, server: Server, token: str, password: str, folder: str, keep: int):
        super().__init__(server, token)
        self.password = password
        self.folder = folder
        self.keep = keep
        self.job_id = ""
        self.pruned: list[StoredBackup] = []

    def _run(self) -> None:
        srv = self.server
        info = self.client.info()
        apply_info(srv, info.get("server") or {})
        self._check_stop()
        # Plesk prepara la copia en su repositorio y luego la exporta a un archivo: durante un rato
        # necesita más o menos el doble de lo que ocupa. Con la copia anterior como medida, se avisa antes
        free = int(info.get("free_bytes") or 0)
        need = 2 * srv.last_size + SERVER_RESERVE
        if srv.last_size and free and free < need and not srv.pending_job:
            raise PleskError("serverspace", str(need - free))
        job = None
        if srv.pending_job:  # una copia anterior quedó en el servidor sin bajar entera
            try:
                job = self.client.job(srv.pending_job)
                if job.get("state") not in ("done", "running", "queued") or job.get("kind") != "backup":
                    job = None
            except PleskError as exc:
                if exc.kind not in ("missing", "server"):
                    raise
                job = None
        if job is None:
            job = self.client.start_backup(self.password)
            srv.pending_job = str(job["id"])
            srv.pending_stamp = self.wall.strftime(STAMP_FMT)
            self._changed()
        self.job_id = srv.pending_job
        self._set_phase("server")
        try:
            job = self._wait_job(self.job_id)
        except PleskError as exc:
            if exc.kind not in ("unreachable", "auth", "cert"):   # sin red o sin llave: se retoma después
                # sin copia que bajar: la próxima vez se empieza de cero. Si Plesk falló, el trabajo se
                # queda en el servidor con su registro (la extensión lo enseña y lo borra a los 3 días)
                if exc.kind == "cancelled":
                    self._release()
                srv.pending_job = srv.pending_stamp = ""
                self._changed()
            raise
        if job.get("warning"):
            self.warning = str(job["warning"])
        self._download(job)

    def _release(self) -> None:
        try:
            self.client.release(self.job_id)
        except PleskError:
            pass  # la extensión borra sola lo que lleva días sin recoger

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self)

    def _download(self, job: dict) -> None:
        srv = self.server
        size = int(job.get("size") or 0)
        want = str(job.get("sha256") or "").lower()
        if size <= 0 or len(want) != 64:
            raise PleskError("server", "size/sha256")
        ensure_folder(os.path.dirname(self.folder))
        os.makedirs(self.folder, exist_ok=True)
        stamp = srv.pending_stamp or self.wall.strftime(STAMP_FMT)
        final = os.path.join(self.folder, stamp + BACKUP_EXT)
        part = final + TMP_SUFFIX
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > size:
            os.remove(part)
            have = 0
        free = shutil.disk_usage(self.folder).free
        if free < size - have + SPACE_RESERVE:
            raise PleskError("space", str(size - have + SPACE_RESERVE - free))
        self.bytes_total = size
        h = hashlib.sha256()
        if have:
            self._set_phase("verify")  # lo ya bajado cuenta para el SHA-256
            with open(part, "rb") as fh:
                while True:
                    self._check_stop()
                    block = fh.read(READ_CHUNK * 4)
                    if not block:
                        break
                    h.update(block)
                    self.bytes_done = fh.tell()
        self.resumed_from = have
        self.bytes_done = have
        self._set_phase("download")
        fails = 0
        while self.bytes_done < size:
            self._check_stop()
            try:
                self._fetch(part, h, size)
                fails = 0
            except _Restart:
                # el servidor no admite «Range»: se borra lo bajado y se repite desde cero
                os.remove(part)
                h = hashlib.sha256()
                self.bytes_done = self.resumed_from = 0
            except PleskError as exc:
                if exc.kind != "unreachable":
                    raise
                fails += 1
                if fails > RETRIES:
                    raise
                self.message = exc.detail[:300]
                self._sleep(min(60.0, 2.0 * 2 ** fails))
        self._set_phase("verify")
        got = h.hexdigest()
        if got != want:
            os.remove(part)
            srv.pending_job = ""  # la copia del servidor se vuelve a hacer de cero la próxima vez
            self._changed()
            raise PleskError("verify", f"{got[:12]} ≠ {want[:12]}")
        os.replace(part, final)
        meta = {
            "server": srv.label, "url": srv.url, "plesk_version": srv.plesk_version, "os": srv.os,
            "created": job.get("finished") or self.wall.isoformat(timespec="seconds"),
            "size": size, "sha256": got, "password": bool(self.password), "job": self.job_id,
            "warning": self.warning,
            "tool": "plesk bin pleskbackup server", "app": USER_AGENT,
        }
        with open(final[:-len(BACKUP_EXT)] + META_EXT, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        self.result = StoredBackup(final, datetime.strptime(stamp, STAMP_FMT), size, got, meta)
        self._release()
        srv.pending_job = srv.pending_stamp = ""
        srv.last_ok = datetime.now().isoformat(timespec="seconds")
        srv.last_size = size
        srv.last_error = ""
        self._changed()
        self.pruned = prune(self.folder, self.keep)

    def _fetch(self, part: str, h, size: int) -> None:
        start = self.bytes_done
        headers = {"Range": f"bytes={start}-"} if start else {}
        conn, resp = self.client.open("GET", "download", {"id": self.job_id}, headers=headers, timeout=90)
        try:
            if resp.status not in (200, 206):
                _parse(resp.status, resp.getheader("Content-Type", ""), resp.read())
                raise PleskError("server", f"HTTP {resp.status}")
            if start and resp.status == 200:  # el servidor no admite seguir: se empieza de nuevo
                raise _Restart()
            mode = "ab" if start else "wb"
            with open(part, mode) as out:
                while True:
                    self._check_stop()
                    try:
                        block = resp.read(READ_CHUNK)
                    except (OSError, http.client.HTTPException) as exc:
                        raise PleskError("unreachable", str(exc)) from exc
                    if not block:
                        break
                    out.write(block)
                    h.update(block)
                    self.bytes_done += len(block)
                    if self.bytes_done > size:
                        raise PleskError("server", "size")
                out.flush()
                os.fsync(out.fileno())
            if self.bytes_done < size:
                raise PleskError("unreachable", "connection closed")
        finally:
            conn.close()


class _Restart(Exception):
    """El servidor devolvió el archivo entero en vez de seguir donde iba."""


class RestoreRun(Operation):
    """Sube una copia guardada y la restaura con la herramienta oficial de Plesk.

    mode «server»: «pleskrestore --restore … -level server» (todo el servidor).
    mode «import»: la deja en el Gestor de copias de seguridad de Plesk, para restaurar desde allí
    lo que se quiera (una suscripción, una base de datos…).
    """

    kind = "restore"

    def __init__(self, server: Server, token: str, backup: StoredBackup, password: str, mode: str):
        super().__init__(server, token)
        self.backup = backup
        self.password = password
        self.mode = mode
        self.job_id = ""

    def _run(self) -> None:
        info = self.client.info()
        apply_info(self.server, info.get("server") or {})
        size = os.path.getsize(self.backup.path)
        self.bytes_total = size
        self._set_phase("verify")
        sha = file_sha256(self.backup.path, lambda n: setattr(self, "bytes_done", n), self.stop)
        if self.backup.sha256 and sha != self.backup.sha256:
            raise PleskError("verify", f"{sha[:12]} ≠ {self.backup.sha256[:12]}")
        self._check_stop()
        up = self.client.upload_start(os.path.basename(self.backup.path), size, sha)
        upload_id = str(up["id"])
        self.bytes_done = self.resumed_from = int(up.get("received") or 0)
        self._set_phase("upload")
        fails = 0
        with open(self.backup.path, "rb") as fh:
            while self.bytes_done < size:
                self._check_stop()
                fh.seek(self.bytes_done)
                chunk = fh.read(UPLOAD_CHUNK)
                try:
                    up = self.client.upload_chunk(upload_id, self.bytes_done, chunk)
                    fails = 0
                except PleskError as exc:
                    if exc.kind != "unreachable":
                        raise
                    fails += 1
                    if fails > RETRIES:
                        raise
                    self.message = exc.detail[:300]
                    self._sleep(min(60.0, 2.0 * 2 ** fails))
                    try:  # dónde se quedó de verdad (puede que la parte llegara)
                        up = self.client.upload_start(os.path.basename(self.backup.path), size, sha)
                    except PleskError:
                        continue
                self.bytes_done = int(up.get("received") or 0)
        self._set_phase("restore")
        job = self.client.start_restore(upload_id, self.password, self.mode)
        self.job_id = str(job["id"])
        try:
            self._wait_job(self.job_id, tolerate_down=RESTORE_DOWN_S)
        except PleskError as exc:
            if exc.kind != "auth":
                self._release()
                raise
            # la copia restaurada trae otros ajustes de la extensión: este ordenador ya no está entre
            # los conectados, pero la restauración ya estaba en marcha
            self.warning = "auth"
            return
        self._release()

    def _release(self) -> None:
        try:
            self.client.release(self.job_id)
        except PleskError:
            pass
