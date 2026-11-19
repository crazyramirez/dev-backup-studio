"""Conectar con Personal Cloud Storage con un código, sin escribir contraseñas.

1. discover(): busca nubes en la red de casa por mDNS (servicio «_pcloud._tcp», que anuncia
   pcloud-install-smb.sh). Solo biblioteca estándar: una consulta DNS a 224.0.0.251:5353.
2. claim(): envía el código de 6 cifras que la persona ve en la web (Ajustes → «Conectar un
   ordenador») y recibe la carpeta de red y sus credenciales. El código sirve una vez y caduca a
   los 10 minutos.
3. connect(): guarda las credenciales donde el sistema las usa solo (Windows: Administrador de
   credenciales; macOS: llavero) y abre la carpeta. Devuelve la ruta de destino.
   Linux: la monta con GVFS («gio mount», sin administrador), como al abrir smb:// en el gestor
   de archivos. GVFS no la recuerda al reiniciar, así que la credencial se guarda en un archivo
   que solo puede leer tu usuario y ensure_mounted() la vuelve a montar cuando hace falta.

HTTPS: la nube usa una CA propia que este ordenador no conoce, así que el certificado no se puede
comprobar. Lo que protege es el código: de un solo uso, 10 minutos y anulado tras 5 fallos. La
contraseña que se recibe solo da acceso a la carpeta de copias, nunca al resto de la nube.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import threading
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
SERVICE = "_pcloud._tcp.local"
MDNS = ("224.0.0.251", 5353)


@dataclass(frozen=True)
class Cloud:
    name: str      # nombre del equipo («pcspi»)
    host: str      # dirección para conectar («pcspi.local» o una IP)
    port: int = 443


@dataclass(frozen=True)
class Link:
    host: str
    share: str
    user: str
    password: str
    folder: str


class LinkError(Exception):
    """Error para enseñar tal cual a la persona (ya traducido por quien lo muestra)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind = kind      # code | unreachable | unavailable | busy | connect | nogvfs
        self.detail = detail


# ── búsqueda por mDNS ───────────────────────────────────────────────────────

def _qname(name: str) -> bytes:
    out = b""
    for part in name.rstrip(".").split("."):
        raw = part.encode("utf-8")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _read_name(data: bytes, pos: int, depth: int = 0) -> tuple[str, int]:
    labels = []
    end = None
    while depth < 20:
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = ((n & 0x3F) << 8) | data[pos + 1]
            if end is None:
                end = pos + 2
            pos = ptr
            depth += 1
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if end is not None else pos)


def _parse(data: bytes) -> list[tuple[str, int, object]]:
    """Registros de una respuesta DNS: (nombre, tipo, valor)."""
    out = []
    _id, _flags, qd, an, ns, ar = struct.unpack("!6H", data[:12])
    pos = 12
    for _ in range(qd):
        _n, pos = _read_name(data, pos)
        pos += 4
    for _ in range(an + ns + ar):
        name, pos = _read_name(data, pos)
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[pos:pos + 10])
        pos += 10
        rdata = pos
        pos += rdlen
        if rtype == 12:  # PTR
            out.append((name, 12, _read_name(data, rdata)[0]))
        elif rtype == 33:  # SRV
            _prio, _weight, port = struct.unpack("!HHH", data[rdata:rdata + 6])
            out.append((name, 33, (_read_name(data, rdata + 6)[0], port)))
        elif rtype == 1 and rdlen == 4:  # A
            out.append((name, 1, socket.inet_ntoa(data[rdata:rdata + 4])))
    return out


def discover(timeout: float = 2.5, known=()) -> list[Cloud]:
    """Nubes de la red: las que se anuncian por mDNS y las ya conocidas (known) que responden.

    En Windows se pregunta de dos formas a la vez: con el servicio DNS-SD del sistema (al que el
    cortafuegos deja recibir respuestas) y con una consulta propia, que funciona si el cortafuegos
    permite la app. Lista vacía si no hay ninguna.
    """
    import concurrent.futures as cf

    jobs = [lambda: _discover_socket(timeout)]
    if IS_WIN:
        jobs.append(lambda: _discover_windows(timeout))
    for host in known:
        if host:
            jobs.append(lambda host=host: [Cloud(name=host.split(".")[0], host=host)] if probe(host) else [])
    found: dict[str, Cloud] = {}
    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for fut in [pool.submit(j) for j in jobs]:
            try:
                for c in fut.result(timeout=timeout + 6):
                    found.setdefault(c.host.lower(), c)
            except Exception:  # noqa: BLE001 - una vía que falla no impide las otras
                continue
    return list(found.values())


def probe(host: str, timeout: float = 3.0) -> bool:
    """¿Es una nube Personal Cloud Storage que responde? (lee /api/v1/system/health)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # CA propia de la nube: solo se lee el estado público
    try:
        req = urllib.request.Request(f"https://{host}/api/v1/system/health", headers={"User-Agent": "DEV-Backup-Studio"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return "volumeState" in json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _discover_windows(timeout: float) -> list[Cloud]:
    """Búsqueda DNS-SD con el servicio de Windows 10+ (DnsServiceBrowse)."""
    from ctypes import wintypes

    class _PTR(ctypes.Structure):
        _fields_ = [("pNameHost", wintypes.LPWSTR)]

    class _DATA(ctypes.Union):
        _fields_ = [("PTR", _PTR), ("raw", ctypes.c_byte * 64)]

    class _REC(ctypes.Structure):
        pass

    _REC._fields_ = [("pNext", ctypes.POINTER(_REC)), ("pName", wintypes.LPWSTR), ("wType", wintypes.WORD),
                     ("wDataLength", wintypes.WORD), ("Flags", wintypes.DWORD), ("dwTtl", wintypes.DWORD),
                     ("dwReserved", wintypes.DWORD), ("Data", _DATA)]
    callback_t = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(_REC))

    class _REQ(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("InterfaceIndex", wintypes.ULONG), ("QueryName", wintypes.LPCWSTR),
                    ("pBrowseCallback", callback_t), ("pQueryContext", ctypes.c_void_p)]

    class _CANCEL(ctypes.Structure):
        _fields_ = [("reserved", ctypes.c_void_p)]

    try:
        dnsapi = ctypes.WinDLL("dnsapi")
        browse, cancel_fn, free = dnsapi.DnsServiceBrowse, dnsapi.DnsServiceBrowseCancel, dnsapi.DnsRecordListFree
    except (OSError, AttributeError):
        return []
    browse.argtypes = [ctypes.POINTER(_REQ), ctypes.POINTER(_CANCEL)]
    browse.restype = wintypes.LONG
    cancel_fn.argtypes = [ctypes.POINTER(_CANCEL)]
    cancel_fn.restype = wintypes.LONG
    free.argtypes = [ctypes.POINTER(_REC), ctypes.c_int]
    free.restype = None

    names: set[str] = set()

    def on_record(status, _ctx, rec):
        try:
            node = rec
            while node:
                r = node.contents
                if r.wType == 12 and r.Data.PTR.pNameHost:  # PTR → «pcspi._pcloud._tcp.local»
                    names.add(r.Data.PTR.pNameHost)
                node = r.pNext
        finally:
            if rec:
                free(rec, 1)

    cb = callback_t(on_record)
    _KEEP.append(cb)  # Windows puede llamar aún tras cancelar: la función tiene que seguir viva
    req = _REQ(Version=1, InterfaceIndex=0, QueryName=SERVICE, pBrowseCallback=cb, pQueryContext=None)
    handle = _CANCEL()
    if browse(ctypes.byref(req), ctypes.byref(handle)) != 9506:  # DNS_REQUEST_PENDING
        return []
    time.sleep(timeout)
    cancel_fn(ctypes.byref(handle))
    clouds = []
    for inst in sorted(names):
        name = inst.split(".")[0]
        if name:
            clouds.append(Cloud(name=name, host=name + ".local"))
    return clouds


_KEEP: list = []


def _discover_socket(timeout: float) -> list[Cloud]:
    query = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _qname(SERVICE) + struct.pack("!HH", 12, 1)
    records: list[tuple[str, int, object]] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    except OSError:
        return []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.bind(("", 0))
        sock.settimeout(0.3)
        deadline = time.monotonic() + timeout
        sends = 0
        while time.monotonic() < deadline:
            if sends < 3:
                try:
                    sock.sendto(query, MDNS)
                except OSError:
                    pass
                sends += 1
            try:
                data, _addr = sock.recvfrom(9000)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            try:
                records += _parse(data)
            except (IndexError, struct.error, UnicodeError):
                continue
    finally:
        sock.close()
    instances = {v for n, t, v in records if t == 12 and n.lower() == SERVICE.lower()}
    srv = {n: v for n, t, v in records if t == 33}
    addr = {n.lower(): v for n, t, v in records if t == 1}
    clouds: dict[str, Cloud] = {}
    for inst in sorted(instances):
        target, port = srv.get(inst, ("", 443))
        name = inst.split(".")[0] if inst else ""
        host = target.rstrip(".") or (name + ".local")
        if host.lower() not in clouds:
            clouds[host.lower()] = Cloud(name=name or host, host=host, port=port or 443)
        # sin resolución .local en el sistema, la IP del anuncio sirve igual
        ip = addr.get(host.lower())
        if ip and not _resolves(host):
            clouds[host.lower()] = Cloud(name=name or host, host=ip, port=port or 443)
    return list(clouds.values())


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 445, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


# ── destino en la nube ──────────────────────────────────────────────────────

# Nombre actual de la carpeta de red de Personal Cloud Storage (antes «Copias», que sigue como alias).
SHARE_NAME = "DEV_Backup_Studio"


def unc_parts(path: str) -> tuple[str, str, str] | None:
    """«\\\\nube\\Recurso\\PC» → ("nube", "Recurso", "PC"); None si no es una ruta de red de Windows."""
    if IS_LINUX:
        return gvfs_parts(path)
    if not IS_WIN or not path.startswith(("\\\\", "//")) or path.startswith("\\\\?\\"):
        return None
    bits = path.replace("/", "\\").lstrip("\\").split("\\")
    if len(bits) < 2 or not bits[0] or not bits[1]:
        return None
    return bits[0], bits[1], "\\".join(b for b in bits[2:] if b)


def current_share_path(dst: str) -> str:
    """Si dst va por el nombre antiguo de la carpeta y el nuevo es la misma (misma marca de disco de
    copia), la ruta con el nombre nuevo. Si no, "" (no hay nada que cambiar)."""
    parts = unc_parts(dst)
    if parts is None or not IS_WIN:
        return ""
    host, share, rest = parts
    if share.lower() == SHARE_NAME.lower():
        return ""
    import engine
    old_root = f"\\\\{host}\\{share}\\"
    new_root = f"\\\\{host}\\{SHARE_NAME}\\"
    old_id = engine.read_disk_id(old_root)
    if not old_id or engine.read_disk_id(new_root) != old_id:
        return ""
    return new_root + rest if rest else new_root.rstrip("\\")


# ── canje del código ────────────────────────────────────────────────────────

def normalize_host(text: str) -> str:
    """«https://nube.local/ajustes» → «nube.local»."""
    text = text.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].strip()


def claim(host: str, code: str, device: str, port: int = 443, timeout: float = 15.0) -> Link:
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        raise LinkError("code")
    netloc = host if port == 443 else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]" if port == 443 else f"[{host}]:{port}"
    body = json.dumps({"code": code, "device": device[:64] or "PC"}).encode()
    req = urllib.request.Request(f"https://{netloc}/api/v1/pairing/claim", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "x-pcloud-request": "1",
                                          "User-Agent": "DEV-Backup-Studio"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # CA propia de la nube: ver el docstring del módulo
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 403):
            raise LinkError("code") from exc
        if exc.code == 404:
            raise LinkError("unavailable") from exc
        if exc.code == 429:
            raise LinkError("busy") from exc
        raise LinkError("unreachable", f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LinkError("unreachable", str(getattr(exc, "reason", exc))) from exc
    try:
        link = Link(host=host, share=str(data["share"]), user=str(data["user"]),
                    password=str(data["password"]), folder=str(data["folder"]))
    except (KeyError, TypeError) as exc:
        raise LinkError("unreachable", "respuesta inesperada") from exc
    for value in (link.share, link.user, link.folder):
        if not value or any(c in value for c in '\\/:*?"<>|') or value.strip(". ") != value:
            raise LinkError("unreachable", "respuesta inesperada")
    return link


def device_name() -> str:
    name = os.environ.get("COMPUTERNAME") if IS_WIN else None
    if IS_MAC:
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                                  timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            name = None
    return (name or socket.gethostname().split(".")[0] or "PC")[:64]


# ── guardar credenciales y abrir la carpeta ─────────────────────────────────

if IS_WIN:
    from ctypes import wintypes

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    class _NETRESOURCE(ctypes.Structure):
        _fields_ = [
            ("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD), ("dwDisplayType", wintypes.DWORD),
            ("dwUsage", wintypes.DWORD), ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
            ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR),
        ]

    CRED_TYPE_DOMAIN_PASSWORD = 2
    CRED_PERSIST_LOCAL_MACHINE = 2
    RESOURCETYPE_DISK = 1


def _save_windows(link: Link) -> None:
    """Como «cmdkey /add», pero sin que la contraseña pase por la línea de órdenes."""
    blob = link.password.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIAL()
    cred.Type = CRED_TYPE_DOMAIN_PASSWORD
    cred.TargetName = link.host
    cred.Comment = "Personal Cloud Storage (DEV Backup Studio)"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = link.user
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    if not advapi.CredWriteW(ctypes.byref(cred), 0):
        raise LinkError("connect", str(ctypes.WinError(ctypes.get_last_error())))
    # Abre ya la sesión con esas credenciales (sin letra de unidad).
    mpr = ctypes.WinDLL("mpr", use_last_error=True)
    mpr.WNetAddConnection2W.argtypes = [ctypes.POINTER(_NETRESOURCE), wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.DWORD]
    mpr.WNetAddConnection2W.restype = wintypes.DWORD
    res = _NETRESOURCE()
    res.dwType = RESOURCETYPE_DISK
    res.lpRemoteName = f"\\\\{link.host}\\{link.share}"
    err = mpr.WNetAddConnection2W(ctypes.byref(res), link.password, link.user, 0)
    # 1219: ya hay una sesión con ese servidor (con estas u otras credenciales); se comprueba abajo
    if err not in (0, 1219):
        raise LinkError("connect", str(ctypes.WinError(err)))


def _mount_mac(link: Link) -> str:
    """Monta el recurso en /Volumes (y el llavero recuerda la contraseña). Devuelve el punto de montaje."""
    def q(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = (f'mount volume "smb://{q(link.host)}/{q(link.share)}" '
              f'as user name "{q(link.user)}" with password "{q(link.password)}"')
    try:
        # el guion va por la entrada estándar: la contraseña no aparece en la lista de procesos
        run = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LinkError("connect", str(exc)) from exc
    if run.returncode != 0:
        raise LinkError("connect", run.stderr.strip())
    out = run.stdout.strip()  # «file DEV_Backup_Studio», o el alias del volumen
    mount = os.path.join("/Volumes", link.share)
    if os.path.isdir(mount):
        return mount
    name = out.split(" ", 1)[1] if " " in out else link.share
    return os.path.join("/Volumes", name.rstrip(":"))


# ── Linux: GVFS ─────────────────────────────────────────────────────────────

APP_NAME = "DEV Backup Studio"
_PROMPT = re.compile(r"(?:^|\n)(user|domain|password)(?: \[[^\]\n]*\])?:[ \t]*$", re.IGNORECASE)
_mount_lock = threading.Lock()
_last_try: dict[str, float] = {}
REMOUNT_EVERY = 60.0  # segundos entre intentos de volver a montar una carpeta que no responde


def _gvfs_dir() -> str:
    return f"/run/user/{os.getuid()}/gvfs"


def _gvfs_fields(name: str) -> dict[str, str]:
    """«smb-share:server=nube.local,share=dev_backup_studio,user=pc» → {"server": …, "share": …, "user": …}."""
    if not name.startswith("smb-share:"):
        return {}
    return dict(f.split("=", 1) for f in name.split(":", 1)[1].split(",") if "=" in f)


def gvfs_parts(path: str) -> tuple[str, str, str] | None:
    """«/run/user/1000/gvfs/smb-share:server=nube.local,share=x/PC» → ("nube.local", "x", "PC")."""
    base = _gvfs_dir() + os.sep
    path = os.path.abspath(path)
    if not path.startswith(base):
        return None
    parts = path[len(base):].split(os.sep)
    fields = _gvfs_fields(parts[0])
    if not fields.get("server") or not fields.get("share"):
        return None
    return fields["server"], fields["share"], os.sep.join(p for p in parts[1:] if p)


def gvfs_path(host: str, share: str) -> str | None:
    """Carpeta donde GVFS tiene montada smb://host/share, o None si no está montada."""
    try:
        names = os.listdir(_gvfs_dir())
    except OSError:
        return None
    for name in names:
        f = _gvfs_fields(name)
        if f.get("server", "").lower() == host.lower() and f.get("share", "").lower() == share.lower():
            return os.path.join(_gvfs_dir(), name)
    return None


def _gio_mount(link: Link, timeout: float = 45.0) -> None:
    """«gio mount smb://host/recurso» contestando a sus preguntas por una terminal virtual: así la
    contraseña no aparece en la lista de procesos ni pasa por la línea de órdenes."""
    gio = shutil.which("gio")
    if not gio:
        raise LinkError("nogvfs", "gio")
    import pty
    import select
    master, slave = pty.openpty()
    env = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="C")  # preguntas en inglés, siempre iguales
    try:
        proc = subprocess.Popen([gio, "mount", f"smb://{link.host}/{link.share}"], stdin=slave, stdout=slave,
                                stderr=slave, env=env, start_new_session=True, close_fds=True)
    except OSError as exc:
        os.close(master)
        os.close(slave)
        raise LinkError("nogvfs", str(exc)) from exc
    os.close(slave)
    answers = {"user": link.user, "domain": "", "password": link.password}  # dominio: el que propone
    asked: dict[str, int] = {}
    out, pending = "", ""
    deadline = time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                raise LinkError("connect", "gio mount: sin respuesta")
            ready, _w, _x = select.select([master], [], [], 0.3)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 4096)
            except OSError:  # EIO: gio ya terminó y cerró la terminal
                data = b""
            if not data:
                break
            text = data.decode("utf-8", "replace")
            out += text
            pending += text
            m = _PROMPT.search(pending)
            if m:
                key = m.group(1).lower()
                asked[key] = asked.get(key, 0) + 1
                if asked[key] > 1:  # vuelve a preguntar: no aceptó el usuario o la contraseña
                    raise LinkError("connect", "usuario o contraseña no aceptados")
                os.write(master, (answers[key] + "\n").encode())
                pending = ""
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)
    if len(link.password) >= 6:  # por si apareciera en un mensaje (no se repite al teclearla)
        out = out.replace(link.password, "•••")
    if proc.returncode != 0 and "already mounted" not in out.lower():
        low = out.lower()
        last = next((ln.strip() for ln in reversed(out.splitlines()) if ln.strip()), "")
        # sin el módulo SMB de GVFS (gvfs-backends, gvfs-smb) o sin GVFS (escritorios sin él)
        if any(s in low for s in ("not supported", "no such method", "not mountable", "implement mount")):
            raise LinkError("nogvfs", last)
        raise LinkError("connect", last or f"gio mount {proc.returncode}")


def _mount_linux(link: Link) -> str:
    """Monta el recurso con GVFS (si no lo está ya) y devuelve su carpeta."""
    with _mount_lock:
        path = gvfs_path(link.host, link.share)
        if path is None:
            _gio_mount(link)
            deadline = time.monotonic() + 10
            while path is None and time.monotonic() < deadline:
                time.sleep(0.3)
                path = gvfs_path(link.host, link.share)
        if path is None:
            raise LinkError("nogvfs", _gvfs_dir())  # montado, pero sin carpeta FUSE (falta gvfs-fuse)
        return path


def credentials_file() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME, "nube-credenciales.json")


def _load_creds() -> dict:
    try:
        with open(credentials_file(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_linux(link: Link) -> None:
    """Guarda usuario y contraseña del recurso para volver a montarlo tras reiniciar. Archivo 0600:
    solo lo lee tu usuario (como ~/.smbcredentials). Solo da acceso a la carpeta de copias."""
    data = _load_creds()
    data[f"{link.host.lower()}/{link.share.lower()}"] = {"host": link.host, "share": link.share,
                                                          "user": link.user, "password": link.password}
    path = credentials_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ensure_mounted(path: str) -> bool:
    """Linux: si path está en una carpeta de la nube que GVFS ya no tiene montada (tras reiniciar o
    cerrar sesión) y su servidor responde, la vuelve a montar con la credencial guardada. Como mucho
    un intento por minuto. True si la carpeta está disponible."""
    if not IS_LINUX:
        return True
    parts = gvfs_parts(path)
    if parts is None:
        return True
    host, share, _rest = parts
    if gvfs_path(host, share):
        return True
    cred = _load_creds().get(f"{host.lower()}/{share.lower()}")
    if not cred:
        return False
    now = time.monotonic()
    if now - _last_try.get(f"{host}/{share}".lower(), -REMOUNT_EVERY) < REMOUNT_EVERY:
        return False
    _last_try[f"{host}/{share}".lower()] = now
    import engine
    if not engine.host_reachable(host):
        return False
    try:
        _mount_linux(Link(host=cred.get("host") or host, share=cred.get("share") or share,
                          user=cred.get("user", ""), password=cred.get("password", ""), folder=""))
    except LinkError:
        return False
    return True


def connect(link: Link) -> str:
    """Guarda las credenciales, abre la carpeta y crea la del ordenador. Devuelve la ruta de destino."""
    if IS_WIN:
        _save_windows(link)
        base = f"\\\\{link.host}\\{link.share}"
    elif IS_MAC:
        base = _mount_mac(link)
    elif IS_LINUX:
        base = _mount_linux(link)
        _save_linux(link)
    else:
        raise LinkError("connect", "sistema no admitido")
    dst = os.path.join(base, link.folder)
    deadline = time.monotonic() + 15
    while True:
        try:
            os.makedirs(dst, exist_ok=True)
            return dst
        except OSError as exc:
            if time.monotonic() > deadline:
                raise LinkError("connect", str(exc)) from exc
            time.sleep(1)
