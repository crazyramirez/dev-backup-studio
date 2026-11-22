"""Copia de Google Drive al disco de copia, sin la app de Google: la API oficial de Drive (v3).

Conectar: OAuth 2.0 para apps de escritorio. Se abre el navegador, la persona entra en Google y da
permiso de solo lectura (drive.readonly); Google redirige a 127.0.0.1 (un servidor de un solo uso) con
un código que se canjea con PKCE. La llave (refresh token) se guarda en vault.Secrets: cifrada con
DPAPI en Windows, en un archivo 0600 en macOS y Linux.

La app no trae ID de cliente de Google: cada persona crea el suyo en Google Cloud (tipo «App de
escritorio», ver el README) y lo pone en Ajustes → Google Drive. Así nadie depende del proyecto de
Google de otro ni de sus límites. (DBS_GOOGLE_CLIENT_ID/_SECRET en el entorno: solo para pruebas.)

Qué se copia: Mi unidad, cada unidad compartida y «Compartido conmigo», con casillas a cualquier nivel
(se excluyen carpetas o archivos sueltos). Los Documentos, Hojas y Presentaciones de Google se exportan
a .docx, .xlsx y .pptx, y los dibujos a .png; formularios, sitios, mapas y accesos directos no tienen
archivo y se omiten.

Dónde: GOOGLE DRIVE/<cuenta>/{Mi unidad, Unidades compartidas/<nombre>, Compartido conmigo} en la raíz
del disco de copia. Solo se descarga lo nuevo o cambiado (MD5 de Google; la fecha para lo exportado),
se comprueba el MD5 al bajarlo y, si se corta, sigue donde iba. Lo que se borra o cambia en Drive se
aparta a .devbackup/versiones/<fecha>/GOOGLE DRIVE/<cuenta>/…, como en la sincronización. Lo que se
desmarca no se toca en el disco.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import http.server
import json
import os
import queue
import re
import secrets as _secrets
import shutil
import sys
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import engine
import vault

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
# guardar copias en Drive: solo los archivos y carpetas que crea la propia app (permiso no sensible)
SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
WRITE_SCOPES = {SCOPE_FILE, "https://www.googleapis.com/auth/drive"}
ROOT_DIR = "GOOGLE DRIVE"          # carpeta en la raíz del disco de copia (engine no la sincroniza)
ASSETS = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "gdrive")
USER_AGENT = "DEV-Backup-Studio"
FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,trashed"
LIST_THREADS = 6
DOWNLOAD_THREADS = 4
RETRIES = 6
READ_CHUNK = 1024 * 1024
SCHEDULES = vault.SCHEDULES
SCHEDULE_DAYS = vault.SCHEDULE_DAYS
# protección contra borrados masivos, como en la sincronización: si de golpe desaparece mucho de Drive
# (un permiso retirado, una unidad que ya no se ve), no se aparta nada sin confirmarlo
MASS_MIN_FILES = 50
MASS_RATIO = 0.25

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
EXPORTS = {  # tipo de Google → (tipo de exportación, extensión)
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}

# las tres partes de Drive (nombre de su carpeta en el disco: se traduce al enseñarlo, no en el disco)
MY_DRIVE, SHARED_DRIVES, SHARED_WITH_ME = "Mi unidad", "Unidades compartidas", "Compartido conmigo"


class DriveError(Exception):
    """kind: noclient | auth | denied | unreachable | quota | server | space | cancelled"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


# ── credenciales de la app en Google ────────────────────────────────────────

def client_credentials(own_id: str = "", own_secret: str = "") -> tuple[str, str]:
    """(client_id, client_secret): el que la persona puso en los ajustes (o el del entorno, en pruebas)."""
    if own_id:
        return own_id.strip(), own_secret.strip()
    return os.environ.get("DBS_GOOGLE_CLIENT_ID", ""), os.environ.get("DBS_GOOGLE_CLIENT_SECRET", "")


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ── conectar: OAuth con el navegador ────────────────────────────────────────

class Authorizer:
    """Un inicio de sesión con Google. start() devuelve la dirección que hay que abrir en el navegador;
    wait() espera a que Google redirija a 127.0.0.1 y devuelve los tokens."""

    def __init__(self, client_id: str, client_secret: str, page_ok: str, page_fail: str,
                 auth_url: str = AUTH_URL, token_url: str = TOKEN_URL, scopes: list[str] | None = None,
                 login_hint: str = ""):
        if not client_id:
            raise DriveError("noclient")
        self.client_id, self.client_secret = client_id, client_secret
        # se piden solo los permisos que hacen falta; los que la cuenta ya dio se conservan
        # (include_granted_scopes), así la llave nueva sirve para todo lo de antes
        self.scopes = list(scopes or [SCOPE])
        self.login_hint = login_hint
        self.auth_url, self.token_url = auth_url, token_url
        self.page_ok, self.page_fail = page_ok, page_fail
        self.state = _secrets.token_urlsafe(24)
        self.verifier = _secrets.token_urlsafe(64)
        self.result: queue.Queue = queue.Queue()
        self.server: http.server.HTTPServer | None = None
        self.redirect_uri = ""

    def start(self) -> str:
        auth = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):  # noqa: N802
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                if "code" not in q and "error" not in q:  # favicon y similares
                    self.send_response(404)
                    self.end_headers()
                    return
                ok = q.get("state") == auth.state and "code" in q
                page = (auth.page_ok if ok else auth.page_fail).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                auth.result.put(q if ok else {"error": q.get("error") or "state"})

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.redirect_uri = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, name="google-oauth", daemon=True).start()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": " ".join(self.scopes), "access_type": "offline", "prompt": "consent", "state": self.state,
            "code_challenge": challenge, "code_challenge_method": "S256", "include_granted_scopes": "true",
        }
        if self.login_hint:
            params["login_hint"] = self.login_hint
        return self.auth_url + "?" + urllib.parse.urlencode(params)

    def wait(self, timeout: float = 600.0, stop: threading.Event | None = None) -> dict:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if stop is not None and stop.is_set():
                    raise DriveError("cancelled")
                try:
                    q = self.result.get(timeout=0.5)
                    break
                except queue.Empty:
                    if time.monotonic() > deadline:
                        raise DriveError("cancelled", "timeout")
        finally:
            self.close()
        if "code" not in q:
            raise DriveError("denied", q.get("error", ""))
        data = _post_form(self.token_url, {
            "code": q["code"], "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code_verifier": self.verifier,
        })
        if not data.get("refresh_token"):
            raise DriveError("auth", "no refresh_token")
        granted = set(str(data.get("scope") or "").split())
        full = "https://www.googleapis.com/auth/drive" in granted
        if granted and not full and any(sc not in granted for sc in self.scopes):
            # en la pantalla de Google se pueden desmarcar permisos: sin el que se pidió no sirve
            raise DriveError("denied", "scope")
        return data

    def close(self) -> None:
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            self.server = None


def _post_form(url: str, form: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(url)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode("utf-8"))
        except ValueError:
            err = {}
        if err.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise DriveError("auth", err.get("error_description") or err["error"]) from exc
        raise DriveError("server", f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DriveError("unreachable", str(getattr(exc, "reason", exc))) from exc


def _ssl_ctx(url: str):
    return _ssl_context() if url.startswith("https://") else None


def revoke(token: str, url: str = REVOKE_URL) -> None:
    try:
        _post_form(url, {"token": token}, timeout=10)
    except DriveError:
        pass  # si no se puede, la persona la quita en myaccount.google.com/permissions


# ── la API ──────────────────────────────────────────────────────────────────

class Drive:
    def __init__(self, refresh_token: str, client_id: str, client_secret: str,
                 api: str = API, token_url: str = TOKEN_URL):
        self.refresh_token = refresh_token
        self.client_id, self.client_secret = client_id, client_secret
        self.api, self.token_url = api, token_url
        self._access = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def _token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._access or time.time() > self._expires - 60:
                data = _post_form(self.token_url, {
                    "refresh_token": self.refresh_token, "client_id": self.client_id,
                    "client_secret": self.client_secret, "grant_type": "refresh_token"})
                self._access = str(data.get("access_token") or "")
                self._expires = time.time() + int(data.get("expires_in") or 3600)
                if not self._access:
                    raise DriveError("auth", "no access_token")
            return self._access

    def open(self, path: str, params: dict | None = None, headers: dict | None = None,
             stop: threading.Event | None = None, timeout: float = 60.0):
        """GET con reintentos (límites de Google, cortes de red). Devuelve la respuesta abierta."""
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        refreshed = False
        for attempt in range(RETRIES + 1):
            if stop is not None and stop.is_set():
                raise DriveError("cancelled")
            h = {"Authorization": f"Bearer {self._token()}", "User-Agent": USER_AGENT, **(headers or {})}
            try:
                return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout,
                                              context=_ssl_ctx(url))
            except urllib.error.HTTPError as exc:
                reason = _error_reason(exc)
                if exc.code == 401 and not refreshed:
                    refreshed = True
                    self._token(force=True)
                    continue
                if exc.code == 429 or exc.code >= 500 or reason in ("rateLimitExceeded", "userRateLimitExceeded",
                                                                     "backendError"):
                    if attempt < RETRIES:
                        _sleep(min(64.0, 2.0 ** attempt + _secrets.randbelow(1000) / 1000), stop)
                        continue
                    raise DriveError("quota", reason or f"HTTP {exc.code}") from exc
                if exc.code == 401:
                    raise DriveError("auth", reason) from exc
                if exc.code == 416:
                    raise _RangeDone() from exc
                raise DriveError("denied" if exc.code in (403, 404) else "server",
                                 reason or f"HTTP {exc.code}") from exc
            except (urllib.error.URLError, OSError) as exc:
                if attempt < RETRIES:
                    _sleep(min(60.0, 2.0 * 2 ** attempt), stop)
                    continue
                raise DriveError("unreachable", str(getattr(exc, "reason", exc))) from exc
        raise DriveError("unreachable")

    def get(self, path: str, params: dict | None = None, stop: threading.Event | None = None) -> dict:
        with self.open(path, params, stop=stop) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def about(self) -> dict:
        return self.get("/about", {"fields": "user(displayName,emailAddress,permissionId)"})

    def shared_drives(self) -> list[dict]:
        out, token = [], ""
        while True:
            params = {"pageSize": 100, "fields": "nextPageToken,drives(id,name)"}
            if token:
                params["pageToken"] = token
            data = self.get("/drives", params)
            out += data.get("drives") or []
            token = data.get("nextPageToken") or ""
            if not token:
                return sorted(out, key=lambda d: d.get("name", "").lower())

    def list(self, query: str, drive_id: str = "", stop: threading.Event | None = None) -> list[dict]:
        """Todos los resultados de una búsqueda (de 1000 en 1000)."""
        out, token = [], ""
        while True:
            params = {"q": query, "pageSize": 1000, "fields": f"nextPageToken,files({FIELDS})",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "orderBy": "folder,name"}
            if drive_id:
                params.update(corpora="drive", driveId=drive_id)
            if token:
                params["pageToken"] = token
            data = self.get("/files", params, stop)
            out += [f for f in data.get("files") or [] if not f.get("trashed")]
            token = data.get("nextPageToken") or ""
            if not token:
                return out

    def children(self, folder_id: str, drive_id: str = "", stop: threading.Event | None = None) -> list[dict]:
        return self.list(f"'{folder_id}' in parents and trashed = false", drive_id, stop)

    def shared_with_me(self, stop: threading.Event | None = None) -> list[dict]:
        return self.list("sharedWithMe = true and trashed = false", "", stop)


class _RangeDone(Exception):
    """416: lo que se pidió seguir ya estaba entero."""


def _error_reason(exc: urllib.error.HTTPError) -> str:
    try:
        err = json.loads(exc.read().decode("utf-8")).get("error") or {}
        errors = err.get("errors") or [{}]
        return str(errors[0].get("reason") or err.get("status") or err.get("message") or "")
    except (ValueError, AttributeError, OSError):
        return ""


def _sleep(seconds: float, stop: threading.Event | None) -> None:
    if stop is None:
        time.sleep(seconds)
    elif stop.wait(seconds):
        raise DriveError("cancelled")


# ── cuentas y selección ─────────────────────────────────────────────────────

@dataclass
class Account:
    id: str                         # permissionId de Google (estable aunque cambie el correo)
    email: str
    name: str = ""
    my_drive: bool = True           # qué partes se copian
    shared_with_me: bool = False
    drives: dict = field(default_factory=dict)   # id de unidad compartida → ¿se copia? (las nuevas sí)
    excluded: list = field(default_factory=list)  # ids de carpetas o archivos desmarcados
    schedule: str = "weekly"
    scopes: list = field(default_factory=lambda: [SCOPE])  # permisos dados (las de antes: solo lectura)
    last_ok: str = ""
    last_size: int = 0
    last_files: int = 0
    last_try: str = ""
    last_error: str = ""

    @property
    def folder_name(self) -> str:
        return vault.safe_name(self.email or self.id)

    def drive_selected(self, drive_id: str) -> bool:
        return bool(self.drives.get(drive_id, True))

    @property
    def can_read(self) -> bool:
        """¿Se puede copiar este Drive al disco? (permiso de lectura)"""
        return SCOPE in self.scopes or "https://www.googleapis.com/auth/drive" in self.scopes

    @property
    def can_write(self) -> bool:
        """¿Se pueden guardar copias en este Drive? (permiso para sus propios archivos)"""
        return bool(WRITE_SCOPES & set(self.scopes))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Account | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        names = set(cls.__dataclass_fields__)
        acc = cls(**{k: v for k, v in data.items() if k in names})
        if acc.schedule not in SCHEDULES:
            acc.schedule = "weekly"
        acc.drives = dict(acc.drives or {})
        acc.excluded = list(acc.excluded or [])
        acc.scopes = sorted({str(x) for x in (acc.scopes or [SCOPE])})
        return acc


def token_key(account_id: str) -> str:
    return f"gdrive/{account_id}/token"


def gdrive_root(volume_root: str) -> str:
    return os.path.join(volume_root, ROOT_DIR)


# ── nombres de archivo ──────────────────────────────────────────────────────

_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_part(name: str) -> str:
    """Un nombre de Drive que sirva en cualquier disco (en Drive vale casi todo, también «/»)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip()
    name = name.rstrip(" .") or "_"
    if name.split(".")[0].lower() in _WIN_RESERVED:
        name = "_" + name
    return name[:200]


def disk_names(items: list[dict]) -> dict[str, str]:
    """id → nombre en el disco. Drive admite nombres repetidos en una carpeta (y que solo cambien las
    mayúsculas): el primero se queda el nombre y los demás llevan su id. Siempre el mismo para cada uno."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for it in sorted(items, key=lambda i: (str(i.get("name", "")).lower(), i["id"])):
        name = safe_part(str(it.get("name", "")))
        ext = EXPORTS.get(it.get("mimeType", ""), ("", ""))[1]
        if ext and not name.lower().endswith(ext):
            name += ext
        key = name.lower()
        if key in taken:
            stem, dot, suffix = name.rpartition(".")
            name = f"{stem} ({it['id'][:8]}).{suffix}" if dot and stem else f"{name} ({it['id'][:8]})"
            key = name.lower()
        taken.add(key)
        out[it["id"]] = name
    return out


def _mtime(it: dict) -> float:
    try:
        return datetime.fromisoformat(str(it.get("modifiedTime", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ── la copia ────────────────────────────────────────────────────────────────

@dataclass
class Remote:
    rel: str           # ruta en el disco, bajo la carpeta de la cuenta
    id: str
    mime: str
    size: int          # -1 si no se sabe (lo exportado)
    md5: str
    mtime: float
    export: str = ""   # tipo al que se exporta ("" = se descarga tal cual)


class DriveSync:
    """Una copia de una cuenta de Drive. Se ejecuta en un hilo; la interfaz solo lee sus campos."""

    def __init__(self, account: Account, drive: Drive, folder: str, meta_root: str, index_path: str,
                 allow_mass_delete: bool = False):
        self.account = account
        self.drive = drive
        self.folder = folder                  # …/GOOGLE DRIVE/<cuenta>
        self.meta_root = meta_root            # raíz del disco de copia (para .devbackup/versiones)
        self.index_path = index_path
        self.allow_mass_delete = allow_mass_delete
        self.phase = "connect"                # connect | scan | download | done | failed | cancelled
        self.error: DriveError | None = None
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.wall = datetime.now()
        self.folders_scanned = 0
        self.files_found = 0
        self.bytes_found = 0
        self.to_get: list[Remote] = []
        self.bytes_total = 0
        self._bytes_finished = 0              # de los archivos ya bajados (lo de los que bajan: active)
        self.files_done = 0
        self.unchanged = 0
        self.versioned = 0                    # apartados a versiones (borrados o cambiados en Drive)
        self.skipped: list[tuple[str, str]] = []   # (ruta, motivo): formularios, accesos directos…
        self.errors: list[tuple[str, str]] = []
        self.held: list[str] = []             # borrados retenidos por la protección contra borrados masivos
        self.active: dict[str, list] = {}     # ruta → [tamaño, bajado]
        self._lock = threading.Lock()
        self._version_root = ""

    @property
    def running(self) -> bool:
        return self.phase not in ("done", "failed", "cancelled")

    @property
    def bytes_done(self) -> int:
        with self._lock:
            return self._bytes_finished + sum(max(0, a[1]) for a in self.active.values())

    def cancel(self) -> None:
        self.stop.set()

    def run(self) -> None:
        try:
            self._run()
            self.phase = "done"
        except DriveError as exc:
            self.error = exc
            self.phase = "cancelled" if exc.kind == "cancelled" else "failed"
        except Exception as exc:  # noqa: BLE001 - se enseña y se puede repetir
            self.error = DriveError("server", f"{type(exc).__name__}: {exc}")
            self.phase = "failed"

    # ── recorrido ──
    def _run(self) -> None:
        acc = self.account
        self.drive.about()  # la llave sigue valiendo
        self.phase = "scan"
        remote: dict[str, Remote] = {}
        excluded_prefixes: list[str] = []   # lo desmarcado: no se toca en el disco
        roots: list[tuple[str, str, str]] = []   # (ruta en el disco, id de carpeta, id de unidad)
        if acc.my_drive:
            roots.append((MY_DRIVE, "root", ""))
        else:
            excluded_prefixes.append(MY_DRIVE)
        try:
            drives = self.drive.shared_drives()
        except DriveError as exc:
            if exc.kind != "denied":
                raise
            drives = []   # cuentas personales: no tienen unidades compartidas
        names = disk_names([{"id": d["id"], "name": d.get("name", ""), "mimeType": FOLDER} for d in drives])
        for d in drives:
            rel = os.path.join(SHARED_DRIVES, names[d["id"]])
            if acc.drive_selected(d["id"]) and d["id"] not in acc.excluded:
                roots.append((rel, d["id"], d["id"]))
            else:
                excluded_prefixes.append(rel)
        excluded = set(acc.excluded)
        work: queue.Queue = queue.Queue()
        for rel, fid, did in roots:
            work.put((rel, fid, did, None))
        if acc.shared_with_me:
            items = self.drive.shared_with_me(self.stop)
            self._add_items(SHARED_WITH_ME, items, "", excluded, remote, excluded_prefixes, work)
        else:
            excluded_prefixes.append(SHARED_WITH_ME)
        self._walk(work, excluded, remote, excluded_prefixes)
        self._check_stop()
        self._sync(remote, excluded_prefixes)

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise DriveError("cancelled")

    def _walk(self, work: queue.Queue, excluded: set, remote: dict, excluded_prefixes: list) -> None:
        """Lista las carpetas en paralelo (cada una es una petición a Google)."""
        pending = [0]
        cond = threading.Condition()
        errors: list[DriveError] = []

        def worker():
            while True:
                with cond:
                    while work.empty() and pending[0] > 0 and not errors and not self.stop.is_set():
                        cond.wait(0.2)
                    if work.empty() or errors or self.stop.is_set():
                        cond.notify_all()
                        return
                    rel, fid, did, _ = work.get()
                    pending[0] += 1
                try:
                    items = self.drive.children(fid, did, self.stop)
                    with self._lock:
                        self.folders_scanned += 1
                    self._add_items(rel, items, did, excluded, remote, excluded_prefixes, work)
                except DriveError as exc:
                    if exc.kind == "denied":   # una carpeta que ya no se puede leer: se cuenta y se sigue
                        with self._lock:
                            self.errors.append((rel, exc.detail or exc.kind))
                    else:
                        errors.append(exc)
                finally:
                    with cond:
                        pending[0] -= 1
                        cond.notify_all()

        threads = [threading.Thread(target=worker, name=f"drive-listar-{i}", daemon=True) for i in range(LIST_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        self._check_stop()

    def _add_items(self, rel: str, items: list[dict], drive_id: str, excluded: set, remote: dict,
                   excluded_prefixes: list, work: queue.Queue) -> None:
        names = disk_names(items)
        for it in items:
            path = os.path.join(rel, names[it["id"]])
            mime = it.get("mimeType", "")
            if it["id"] in excluded:
                with self._lock:
                    excluded_prefixes.append(path)
                continue
            if mime == FOLDER:
                work.put((path, it["id"], drive_id, None))
                continue
            if mime == SHORTCUT:
                with self._lock:
                    self.skipped.append((path, "shortcut"))
                continue
            export = ""
            if mime.startswith("application/vnd.google-apps."):
                if mime not in EXPORTS:
                    with self._lock:
                        self.skipped.append((path, "google"))   # formularios, sitios, mapas…
                    continue
                export = EXPORTS[mime][0]
            size = int(it["size"]) if it.get("size") not in (None, "") and not export else -1
            r = Remote(path, it["id"], mime, size, str(it.get("md5Checksum") or ""), _mtime(it), export)
            with self._lock:
                remote[engine.norm_rel(path)] = r
                self.files_found += 1
                self.bytes_found += max(0, size)

    # ── comparar con el disco y bajar ──
    def _load_index(self) -> dict:
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self, index: dict) -> None:
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        os.replace(tmp, self.index_path)

    def _sync(self, remote: dict, excluded_prefixes: list) -> None:
        index = self._load_index()   # ruta normalizada → {rel, id, md5, mtime, size}
        excl = [engine.norm_rel(p) for p in excluded_prefixes]

        def under_excluded(key: str) -> bool:
            return any(key == p or key.startswith(p + os.sep) for p in excl)

        for key, r in remote.items():
            old = index.get(key)
            local = os.path.join(self.folder, r.rel)
            if old and self._same(old, r) and os.path.exists(engine.lp(local)):
                self.unchanged += 1
                continue
            if not old and self._local_matches(local, r):   # ya estaba en el disco (otra copia, otra app)
                index[key] = self._entry(r, os.path.getsize(engine.lp(local)))
                self.unchanged += 1
                continue
            self.to_get.append(r)
        gone = [k for k in index if k not in remote and not under_excluded(k)]
        if gone and not self.allow_mass_delete:
            total = max(1, len(index))
            if len(gone) >= MASS_MIN_FILES and len(gone) / total >= MASS_RATIO:
                self.held = [index[k]["rel"] for k in gone]
                gone = []
        self.bytes_total = sum(max(0, r.size) for r in self.to_get)
        need = self.bytes_total + vault.SPACE_RESERVE
        os.makedirs(self.folder, exist_ok=True)
        free = shutil.disk_usage(self.folder).free
        if free < need:
            raise DriveError("space", str(need - free))
        self._version_root = os.path.join(self.meta_root, engine.META_DIR, "versiones",
                                          self.wall.strftime(engine.STAMP_FMT), engine.rel_to_volume(self.folder))
        # lo que ya no está en Drive: a versiones
        for key in gone:
            local = os.path.join(self.folder, index[key]["rel"])
            if os.path.exists(engine.lp(local)):
                try:
                    self._to_versions(local, index[key]["rel"])
                except OSError as exc:
                    self.errors.append((index[key]["rel"], str(exc)))
                    continue
            del index[key]
        self._save_index(index)
        self.phase = "download"
        q: queue.Queue = queue.Queue()
        for r in sorted(self.to_get, key=lambda r: r.size):  # lo pequeño primero: avanza a la vista
            q.put(r)
        saved = [time.monotonic()]

        def worker():
            while not self.stop.is_set():
                try:
                    r = q.get_nowait()
                except queue.Empty:
                    return
                try:
                    size = self._download(r)
                    with self._lock:
                        index[engine.norm_rel(r.rel)] = self._entry(r, size)
                        self.files_done += 1
                        if time.monotonic() - saved[0] > 30:   # que un corte no obligue a repetirlo todo
                            saved[0] = time.monotonic()
                            self._save_index(dict(index))
                except DriveError as exc:
                    if exc.kind in ("cancelled", "auth", "space"):
                        if exc.kind != "cancelled":   # sin llave o sin sitio: se para todo
                            with self._lock:
                                self.error = self.error or exc
                            self.stop.set()
                        return
                    with self._lock:
                        self.errors.append((r.rel, exc.detail or exc.kind))
                except OSError as exc:
                    with self._lock:
                        self.errors.append((r.rel, str(exc)))

        threads = [threading.Thread(target=worker, name=f"drive-bajar-{i}", daemon=True)
                   for i in range(DOWNLOAD_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self._save_index(index)
        if self.error:
            raise self.error
        self._check_stop()

    @staticmethod
    def _entry(r: Remote, size: int) -> dict:
        return {"rel": r.rel, "id": r.id, "md5": r.md5, "mtime": r.mtime, "size": size}

    @staticmethod
    def _same(old: dict, r: Remote) -> bool:
        if old.get("id") != r.id:
            return False
        if r.md5:
            return old.get("md5") == r.md5
        return abs(float(old.get("mtime") or 0) - r.mtime) < 1.0   # lo exportado: por fecha

    @staticmethod
    def _local_matches(local: str, r: Remote) -> bool:
        if r.size < 0:
            return False
        try:
            st = os.stat(engine.lp(local))
        except OSError:
            return False
        return st.st_size == r.size and abs(st.st_mtime - r.mtime) < 2.0

    def _to_versions(self, local: str, rel: str) -> None:
        target = os.path.join(self._version_root, rel)
        os.makedirs(engine.lp(os.path.dirname(target)), exist_ok=True)
        os.replace(engine.lp(local), engine.lp(target))
        with self._lock:
            self.versioned += 1

    def _download(self, r: Remote) -> int:
        final = os.path.join(self.folder, r.rel)
        part = final + engine.TMP_SUFFIX
        os.makedirs(engine.lp(os.path.dirname(final)), exist_ok=True)
        with self._lock:
            self.active[r.rel] = [r.size, 0]
        try:
            for attempt in range(RETRIES + 1):
                # cada intento sigue desde lo que de verdad hay en el disco (también tras un corte)
                have, h = 0, hashlib.md5()
                if not r.export and os.path.exists(engine.lp(part)):
                    have = os.path.getsize(engine.lp(part))
                    if 0 <= r.size < have:
                        have = 0
                    else:
                        with open(engine.lp(part), "rb") as fh:
                            while block := fh.read(READ_CHUNK * 4):
                                h.update(block)
                with self._lock:
                    self.active[r.rel][1] = have
                try:
                    have = self._fetch(r, part, have, h)
                    break
                except _Cut:
                    if attempt >= RETRIES:
                        raise DriveError("unreachable", "connection closed") from None
                    _sleep(min(30.0, 2.0 * 2 ** attempt), self.stop)
            if r.md5 and h.hexdigest() != r.md5:
                os.remove(engine.lp(part))
                raise DriveError("server", "MD5")
            if os.path.exists(engine.lp(final)):
                self._to_versions(final, r.rel)   # la versión anterior no se pierde
            os.replace(engine.lp(part), engine.lp(final))
            if r.mtime:
                os.utime(engine.lp(final), (r.mtime, r.mtime))
            with self._lock:
                self._bytes_finished += have
            return have
        finally:
            with self._lock:
                self.active.pop(r.rel, None)

    def _fetch(self, r: Remote, part: str, have: int, h) -> int:
        if r.export:
            path, params, headers = f"/files/{r.id}/export", {"mimeType": r.export}, {}
            have = 0
        else:
            path = f"/files/{r.id}"
            params = {"alt": "media", "supportsAllDrives": "true"}
            headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            resp = self.drive.open(path, params, headers, self.stop, timeout=120)
        except _RangeDone:
            return have
        except DriveError as exc:
            if r.export and exc.kind == "denied" and "exportSizeLimit" in exc.detail:
                raise DriveError("denied", "export too large") from exc
            raise
        with resp:
            if have and resp.status != 206:   # no siguió donde iba: se empieza de cero
                have = 0
                h.__init__()
            with open(engine.lp(part), "ab" if have else "wb") as out:
                while True:
                    if self.stop.is_set():
                        raise DriveError("cancelled")
                    try:
                        block = resp.read(READ_CHUNK)
                    except (OSError, http.client.HTTPException) as exc:
                        raise _Cut() from exc
                    if not block:
                        break
                    out.write(block)
                    h.update(block)
                    have += len(block)
                    with self._lock:
                        self.active[r.rel][1] = have
        if r.size >= 0 and have < r.size:
            raise _Cut()
        return have


class _Cut(Exception):
    """La descarga se cortó a medias: se sigue donde iba."""



def ensure_folder(root: str) -> str:
    """La carpeta GOOGLE DRIVE con el icono de Google Drive."""
    return vault.ensure_folder(root, ASSETS, "gdrive", "Google Drive")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
