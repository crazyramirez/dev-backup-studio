"""Conectar con Personal Cloud Storage con un código, sin escribir contraseñas.

1. discover(): busca nubes en la red de casa por mDNS (servicio «_pcloud._tcp», que anuncia
   pcloud-install-smb.sh). Solo biblioteca estándar: una consulta DNS a 224.0.0.251:5353.
2. claim(): envía el código de 6 cifras que la persona ve en la web (Ajustes → «Conectar un
   ordenador») y recibe la carpeta de red y sus credenciales. El código sirve una vez y caduca a
   los 10 minutos.
3. connect(): guarda las credenciales donde el sistema las usa solo (Windows: Administrador de
   credenciales; macOS: llavero) y abre la carpeta. Devuelve la ruta de destino.

HTTPS: la nube usa una CA propia que este ordenador no conoce, así que el certificado no se puede
comprobar. Lo que protege es el código: de un solo uso, 10 minutos y anulado tras 5 fallos. La
contraseña que se recibe solo da acceso a la carpeta de copias, nunca al resto de la nube.
"""

from __future__ import annotations

import ctypes
import json
import os
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
        self.kind = kind      # code | unreachable | unavailable | busy | connect
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


def discover(timeout: float = 2.5) -> list[Cloud]:
    """Nubes que se anuncian en la red. Lista vacía si no hay ninguna (o la red no deja)."""
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


def connect(link: Link) -> str:
    """Guarda las credenciales, abre la carpeta y crea la del ordenador. Devuelve la ruta de destino."""
    if IS_WIN:
        _save_windows(link)
        base = f"\\\\{link.host}\\{link.share}"
    elif IS_MAC:
        base = _mount_mac(link)
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
