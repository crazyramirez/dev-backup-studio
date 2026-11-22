"""Copia a Google Drive: Google Drive como destino de la sincronización.

El destino es una cadena con forma de ruta, «gdrive:/<cuenta>/<equipo>/<carpeta>», y en Drive es la
carpeta fija «DEV Backup Studio/<equipo>/<carpeta>» de Mi unidad, creada por la app. Con el permiso
drive.file la app solo ve y cambia lo que ella misma crea: el resto del Drive ni lo ve ni lo toca.

- El análisis es el del motor (engine.BackupJob: movimientos, carpetas renombradas, exclusiones,
  borrado masivo). Solo cambia de dónde sale «lo que hay en el destino»: del índice local de lo
  subido (rápido) o, la primera vez, cada 7 días o si algo quedó a medias, de Drive mismo (todo lo
  que la app subió a esa carpeta, de 1000 en 1000, gracias a una marca en appProperties).
- Subir: los archivos pequeños en una sola petición; los grandes, en trozos de 8 MB que se
  reanudan (también tras cerrar la app: la sesión de subida dura una semana). Cada archivo se
  comprueba con el MD5 que devuelve Google. La fecha exacta del original va en appProperties (Drive
  guarda milisegundos).
- Mover y renombrar se hace en el servidor, sin volver a subir nada. Lo borrado o sustituido pasa a
  «DEV Backup Studio/.versiones/<fecha>/<equipo>/<carpeta>/…» y se quita pasado el plazo (o antes,
  si falta espacio en Drive).
- Sin conexión, la copia espera y sigue sola, como con un disco que desaparece.
- Google permite subir 750 GB al día por cuenta: al llegar, la copia para y lo dice.

Recuperar (la pestaña «Recuperar»): lo apartado a versiones se lista desde el índice local y se
descarga de vuelta sin sobrescribir nada.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import platform
import re
import secrets as _secrets
import socket
import sqlite3
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import engine
import gdrive
import versions
from engine import FULL_CHECK_DAYS, MODE_MIRROR, STAMP_FMT, TMP_SUFFIX, fold, lp, norm_rel
from gdrive import DriveError
from i18n import _, _n

PREFIX = "gdrive:"
ROOT_NAME = "DEV Backup Studio"
VERSIONS_NAME = ".versiones"
PROP_DEST = "dbs"          # archivo o carpeta de la copia de un destino (valor: su clave)
PROP_VDEST = "dbsv"        # apartado a versiones (ya no cuenta como copia)
PROP_MTIME = "mt"          # fecha exacta del original, en ns
PROP_ROOT = "dbsroot"      # carpetas fijas de la app: «DEV Backup Studio» y «.versiones»
PROP_DESTROOT = "dbsdest"  # la carpeta de un destino
SIMPLE_MAX = 5 * 1024 * 1024
CHUNK = 8 * 1024 * 1024    # múltiplo de 256 KB, como pide Google
UPLOAD_THREADS = 4         # Google admite unas pocas escrituras por segundo: más hilos no van más rápido
DAILY_NEAR = 700 * 10**9   # cerca del límite diario de 750 GB
FIELDS_UP = "id,md5Checksum,size"
FIELDS_LIST = "id,name,mimeType,parents,size,md5Checksum,modifiedTime,appProperties"
VPREFIX = "gdrive-versiones:"  # ruta de mentira de una fecha de versiones (para la pestaña Recuperar)
SEP = re.compile(r"[\\/]+")


# ── el destino ──────────────────────────────────────────────────────────────

def is_dest(path: str | None) -> bool:
    return bool(path) and re.match(r"^gdrive:[\\/]", str(path)) is not None


def make_dest(account_id: str, machine: str, folder: str) -> str:
    return os.path.normpath(f"{PREFIX}/{account_id}/{machine}/{folder}")


def parse_dest(path: str | None) -> tuple[str, str, str] | None:
    """(cuenta, equipo, carpeta) o None si no es un destino de Drive."""
    if not is_dest(path):
        return None
    parts = [p for p in SEP.split(str(path)[len(PREFIX):]) if p]
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 else None


def dest_key(path: str) -> str:
    acc, machine, folder = parse_dest(path) or ("", "", "")
    return hashlib.sha1(f"{acc}/{machine.lower()}/{folder.lower()}".encode("utf-8")).hexdigest()[:16]


def machine_name() -> str:
    name = (platform.node() or socket.gethostname() or "").split(".")[0]
    return gdrive.safe_part(name or "PC")


def folder_for(src: str) -> str:
    """Nombre de la carpeta en Drive para un origen: «D:\\DEV» → «DEV»; una unidad entera → «Disco D»."""
    src = os.path.normpath(src or "")
    name = os.path.basename(src.rstrip("\\/"))
    if not name:
        letter = os.path.splitdrive(src)[0].rstrip(":") or "raiz"
        name = f"Disco {letter}"  # fijo, sin traducir: cambiar el idioma no debe cambiar la carpeta en Drive
    return gdrive.safe_part(name)


def index_path(data_dir: str, dst: str) -> str:
    return os.path.join(data_dir, "estado", f"gdrive-destino-{dest_key(dst)}.db")


def meta_root(data_dir: str, dst: str) -> str:
    """Carpeta local del destino: historial (para el estimador) y sesiones de subida a medias."""
    root = os.path.join(data_dir, "gdrive-destino", dest_key(dst))
    os.makedirs(os.path.join(root, engine.META_DIR), exist_ok=True)
    return root


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else "https://drive.google.com/drive/my-drive"


def online(api: str = gdrive.API, timeout: float = 3.0) -> bool:
    """¿Responde Google? (una conexión TCP, sin esperar a los reintentos de la red)"""
    u = urllib.parse.urlsplit(api)
    try:
        with socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=timeout):
            return True
    except OSError:
        return False


def dest_folder_id(ix_file: str) -> str:
    """La carpeta de la copia en Drive (del índice local), o "" si aún no se ha subido nada."""
    try:
        con = sqlite3.connect(f"file:{ix_file}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT v FROM meta WHERE k = 'dest_id'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return row[0] if row and row[0] else ""


def error_text(exc: DriveError) -> str:
    kind = exc.kind
    if kind == "noclient":
        return _("Falta tu ID de cliente de Google (Ajustes → Google Drive).")
    if kind == "auth":
        return _("Google no acepta la llave de esta cuenta: vuelve a elegir Google Drive como destino para "
                 "conectarla de nuevo.")
    if kind == "denied":
        return _("Google no deja a la app guardar copias en este Drive: vuelve a elegir Google Drive como destino "
                 "y da el permiso.")
    if kind == "unreachable":
        return _("No hay conexión con Google Drive.")
    if kind == "space":
        return _("Tu Google Drive está lleno: libera espacio o amplía el almacenamiento de tu cuenta de Google.")
    if kind == "daily":
        return _("Google permite subir 750 GB al día por cuenta y ya se ha llegado. Lo subido se queda: la copia "
                 "sigue mañana (vuelve a sincronizar o espera a la próxima copia programada).")
    if kind == "quota":
        return _("Google está frenando las peticiones (demasiadas seguidas). Vuelve a intentarlo en unos minutos.")
    if kind == "gone":
        return _("La carpeta de la copia en Google Drive se ha borrado o está en la papelera. Vuelve a sincronizar: "
                 "se sube todo de nuevo.")
    if kind == "cancelled":
        return _("Cancelado")
    return _("Google Drive ha respondido con un error ({detail}).").format(detail=exc.detail or kind)


def _q(text: str) -> str:
    """Texto para una búsqueda de Drive («'» y «\\» escapadas)."""
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def _rfc3339(mtime_ns: int) -> str:
    when = datetime.fromtimestamp(max(0, mtime_ns) / 1e9, tz=timezone.utc)
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mtime_ns(item: dict) -> int:
    props = item.get("appProperties") or {}
    try:
        return int(props[PROP_MTIME])
    except (KeyError, TypeError, ValueError):
        return int(gdrive._mtime(item) * 1e9)


def _reason(raw: bytes) -> str:
    try:
        err = json.loads(raw.decode("utf-8")).get("error") or {}
        errors = err.get("errors") or [{}]
        return str(errors[0].get("reason") or err.get("status") or err.get("message") or "")
    except (ValueError, AttributeError):
        return ""


# ── la API, para escribir ───────────────────────────────────────────────────

class Writer(gdrive.Drive):
    """La API de Drive con escritura. Cada hilo reutiliza su conexión (miles de archivos pequeños)."""

    def __init__(self, refresh_token: str, client_id: str, client_secret: str,
                 api: str = gdrive.API, token_url: str = gdrive.TOKEN_URL):
        super().__init__(refresh_token, client_id, client_secret, api, token_url)
        self.upload_api = api.replace("/drive/v3", "/upload/drive/v3")
        self._tls = threading.local()

    # ── conexión ──
    def _conn(self, url: str):
        u = urllib.parse.urlsplit(url)
        conns = getattr(self._tls, "conns", None)
        if conns is None:
            conns = self._tls.conns = {}
        c = conns.get(u.netloc)
        if c is None:
            if u.scheme == "https":
                c = http.client.HTTPSConnection(u.netloc, timeout=120, context=gdrive._ssl_context())
            else:
                c = http.client.HTTPConnection(u.netloc, timeout=120)
            conns[u.netloc] = c
        return c, u

    def _drop(self, url: str) -> None:
        conns = getattr(self._tls, "conns", None) or {}
        c = conns.pop(urllib.parse.urlsplit(url).netloc, None)
        if c is not None:
            try:
                c.close()
            except OSError:
                pass

    def request(self, method: str, url: str, body: bytes = b"", headers: dict | None = None,
                stop: threading.Event | None = None, expect=(200,), retry: bool = True) -> tuple[int, dict, bytes]:
        """Una petición con reintentos (límites de Google, cortes). Devuelve (estado, cabeceras, cuerpo):
        los estados de «expect» y 308; cualquier otro, tras los reintentos, es un DriveError."""
        refreshed = False
        for attempt in range(gdrive.RETRIES + 1):
            if stop is not None and stop.is_set():
                raise DriveError("cancelled")
            h = {"Authorization": f"Bearer {self._token()}", "User-Agent": gdrive.USER_AGENT,
                 "Content-Length": str(len(body)), **(headers or {})}
            try:
                conn, u = self._conn(url)
                conn.request(method, u.path + ("?" + u.query if u.query else ""), body=body or None, headers=h)
                resp = conn.getresponse()
                raw = resp.read()
                status = resp.status
                rh = {k.lower(): v for k, v in resp.getheaders()}
                if (rh.get("connection") or "").lower() == "close":
                    self._drop(url)
            except (OSError, http.client.HTTPException) as exc:
                self._drop(url)
                if retry and attempt < gdrive.RETRIES:
                    gdrive._sleep(min(30.0, 1.5 * 2 ** attempt), stop)
                    continue
                raise DriveError("unreachable", str(exc)) from exc
            if status in expect or status == 308:
                return status, rh, raw
            reason = _reason(raw)
            if status == 401 and not refreshed:
                refreshed = True
                self._token(force=True)
                continue
            if status == 403 and reason == "storageQuotaExceeded":
                raise DriveError("space", reason)
            if status == 429 or status >= 500 or reason in ("rateLimitExceeded", "userRateLimitExceeded",
                                                            "backendError", "uploadLimitExceeded"):
                if attempt < gdrive.RETRIES:
                    gdrive._sleep(min(64.0, 2.0 ** attempt + _secrets.randbelow(1000) / 1000), stop)
                    continue
                raise DriveError("quota", reason or f"HTTP {status}")
            if status == 401:
                raise DriveError("auth", reason)
            if status == 404:
                raise DriveError("notfound", reason)
            if status == 403:
                raise DriveError("denied", reason)
            raise DriveError("server", reason or f"HTTP {status}")
        raise DriveError("unreachable")

    def call(self, method: str, path: str, params: dict | None = None, meta: dict | None = None,
             stop: threading.Event | None = None, expect=(200,)) -> dict:
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        body = json.dumps(meta).encode("utf-8") if meta is not None else b""
        headers = {"Content-Type": "application/json; charset=UTF-8"} if meta is not None else {}
        _s, _h, raw = self.request(method, url, body, headers, stop, expect)
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}

    # ── operaciones ──
    def list_all(self, query: str, fields: str = FIELDS_LIST, stop: threading.Event | None = None) -> list[dict]:
        out, token = [], ""
        while True:
            params = {"q": query, "pageSize": 1000, "fields": f"nextPageToken,files({fields})", "spaces": "drive"}
            if token:
                params["pageToken"] = token
            data = self.call("GET", "/files", params, stop=stop)
            out += data.get("files") or []
            token = data.get("nextPageToken") or ""
            if not token:
                return out

    def find_folders(self, prop: str, value: str, parent: str = "", name: str = "",
                     stop: threading.Event | None = None) -> list[dict]:
        q = f"appProperties has {{ key='{prop}' and value='{_q(value)}' }} and trashed = false " \
            f"and mimeType = '{gdrive.FOLDER}'"
        if parent:
            q += f" and '{_q(parent)}' in parents"
        if name:
            q += f" and name = '{_q(name)}'"
        found = self.list_all(q, "id,name,parents,createdTime", stop)
        return sorted(found, key=lambda f: (f.get("createdTime") or "", f["id"]))

    def create_folder(self, name: str, parent: str, props: dict, stop: threading.Event | None = None) -> str:
        meta = {"name": name, "mimeType": gdrive.FOLDER, "appProperties": props}
        if parent:
            meta["parents"] = [parent]
        return self.call("POST", "/files", {"fields": "id"}, meta, stop)["id"]

    def move(self, fid: str, new_parent: str = "", old_parent: str = "", name: str | None = None,
             props: dict | None = None, stop: threading.Event | None = None) -> None:
        params = {"fields": "id"}
        if new_parent and new_parent != old_parent:
            params["addParents"] = new_parent
            if old_parent:
                params["removeParents"] = old_parent
        meta: dict = {}
        if name is not None:
            meta["name"] = name
        if props:
            meta["appProperties"] = props
        self.call("PATCH", f"/files/{fid}", params, meta, stop)

    def parents(self, fid: str, stop: threading.Event | None = None) -> list[str]:
        return self.call("GET", f"/files/{fid}", {"fields": "parents"}, stop=stop).get("parents") or []

    def alive(self, fid: str, stop: threading.Event | None = None) -> bool:
        """¿Existe y no está en la papelera?"""
        try:
            return not self.call("GET", f"/files/{fid}", {"fields": "id,trashed"}, stop=stop).get("trashed")
        except DriveError as exc:
            if exc.kind in ("notfound", "denied"):
                return False
            raise

    def delete(self, fid: str, stop: threading.Event | None = None) -> None:
        """Elimina para siempre (lo apartado por la app; una carpeta, con todo lo suyo)."""
        try:
            self.call("DELETE", f"/files/{fid}", stop=stop, expect=(200, 204))
        except DriveError as exc:
            if exc.kind != "notfound":
                raise

    def quota(self) -> tuple[int | None, int]:
        """(límite, usado) en bytes; límite None si es ilimitado."""
        q = self.call("GET", "/about", {"fields": "storageQuota(limit,usage)"}).get("storageQuota") or {}
        limit = int(q["limit"]) if q.get("limit") else None
        return limit, int(q.get("usage") or 0)

    def upload(self, local: str, name: str, parent: str, mtime_ns: int, props: dict, progress,
               stop: threading.Event, wait, sessions: "Sessions") -> dict:
        """Sube un archivo y comprueba su MD5. progress(bytes subidos); wait() en cada trozo (pausa)."""
        size = os.path.getsize(lp(local))
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        meta = {"name": name, "parents": [parent], "mimeType": mime, "modifiedTime": _rfc3339(mtime_ns),
                "appProperties": props}
        if size <= SIMPLE_MAX:
            with open(lp(local), "rb") as fh:
                data = fh.read(SIMPLE_MAX + 1)
            if len(data) != size:
                raise OSError(0, _("el archivo cambió mientras se subía"))
            wait()
            md5 = hashlib.md5(data).hexdigest()
            boundary = "dbs" + _secrets.token_hex(12)
            body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                    + json.dumps(meta).encode("utf-8")
                    + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + data
                    + f"\r\n--{boundary}--\r\n".encode())
            url = self.upload_api + "/files?" + urllib.parse.urlencode({"uploadType": "multipart", "fields": FIELDS_UP})
            _s, _h, raw = self.request("POST", url, body, {"Content-Type": f"multipart/related; boundary={boundary}"},
                                       stop)
            res = json.loads(raw.decode("utf-8"))
            progress(size)
        else:
            res = self._resumable(local, size, meta, mime, progress, stop, wait, sessions)
            md5 = res.pop("_md5")
        if res.get("md5Checksum") and res["md5Checksum"] != md5:
            try:
                self.delete(res["id"])
            except DriveError:
                pass
            raise OSError(0, _("lo subido a Google Drive no coincide con el original (MD5)"))
        return res

    def _status(self, uri: str, size: int, stop) -> tuple[int | None, dict | None]:
        """Cuánto tiene Google de una subida a medias: (siguiente byte, None), (None, archivo) si ya
        está entera o (None, None) si la sesión caducó."""
        try:
            status, h, raw = self.request("PUT", uri, b"", {"Content-Range": f"bytes */{size}"}, stop,
                                          expect=(200, 201))
        except DriveError as exc:
            if exc.kind in ("notfound", "server", "denied"):
                return None, None
            raise
        if status == 308:
            rng = h.get("range") or ""
            return (int(rng.rsplit("-", 1)[1]) + 1 if rng else 0), None
        return None, json.loads(raw.decode("utf-8"))

    def _resumable(self, local, size, meta, mime, progress, stop, wait, sessions) -> dict:
        key = sessions.key(local, size, meta["modifiedTime"])
        uri = sessions.get(key)
        offset = 0
        if uri:
            offset, done = self._status(uri, size, stop)
            if done is not None:  # se había subido entero justo antes del corte
                sessions.drop(key)
                done["_md5"] = _file_md5(local)
                progress(size)
                return done
            if offset is None:
                uri, offset = "", 0
        if not uri:
            url = self.upload_api + "/files?" + urllib.parse.urlencode({"uploadType": "resumable", "fields": FIELDS_UP})
            _s, h, _raw = self.request("POST", url, json.dumps(meta).encode("utf-8"),
                                       {"Content-Type": "application/json; charset=UTF-8",
                                        "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)}, stop)
            uri = h.get("location") or ""
            if not uri:
                raise DriveError("server", "sin sesión de subida")
            sessions.put(key, uri)
        md5 = hashlib.md5()
        with open(lp(local), "rb") as fh:
            left = offset
            while left:  # lo ya subido: solo para el MD5
                part = fh.read(min(left, CHUNK))
                if not part:
                    raise OSError(0, _("el archivo cambió mientras se subía"))
                md5.update(part)
                left -= len(part)
            progress(offset)
            while True:
                wait()
                if stop.is_set():
                    raise DriveError("cancelled")
                data = fh.read(CHUNK)
                if offset + len(data) > size or (not data and offset < size):
                    sessions.drop(key)
                    raise OSError(0, _("el archivo cambió mientras se subía"))
                end = offset + len(data) - 1
                rng = f"bytes {offset}-{end}/{size}" if data else f"bytes */{size}"
                status, h, raw = self.request("PUT", uri, data, {"Content-Range": rng}, stop, expect=(200, 201),
                                              retry=False)
                if status == 308:
                    got = h.get("range") or ""
                    new = int(got.rsplit("-", 1)[1]) + 1 if got else 0
                    accepted = max(0, min(len(data), new - offset))
                    md5.update(data[:accepted])
                    offset += accepted
                    if accepted < len(data):
                        fh.seek(offset)
                    progress(offset)
                    continue
                md5.update(data)
                sessions.drop(key)
                progress(size)
                res = json.loads(raw.decode("utf-8"))
                res["_md5"] = md5.hexdigest()
                return res

    def download(self, fid: str, target: str, stop: threading.Event | None = None, progress=None) -> str:
        """Baja un archivo a «target» (lo crea entero o nada). Devuelve su MD5."""
        tmp = target + TMP_SUFFIX
        h = hashlib.md5()
        try:
            with self.open(f"/files/{fid}", {"alt": "media"}, stop=stop, timeout=120) as resp, open(lp(tmp), "wb") as out:
                done = 0
                while True:
                    if stop is not None and stop.is_set():
                        raise DriveError("cancelled")
                    part = resp.read(gdrive.READ_CHUNK)
                    if not part:
                        break
                    out.write(part)
                    h.update(part)
                    done += len(part)
                    if progress:
                        progress(done)
            os.replace(lp(tmp), lp(target))
        except BaseException:
            try:
                os.remove(lp(tmp))
            except OSError:
                pass
            raise
        return h.hexdigest()


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(lp(path), "rb") as fh:
        for part in iter(lambda: fh.read(CHUNK), b""):
            h.update(part)
    return h.hexdigest()


class Sessions:
    """Subidas grandes a medias (dirección de la sesión de Google), para seguir tras cerrar la app."""
    MAX_AGE = 6 * 86400  # Google las guarda una semana

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            now = time.time()
            self.data = {k: v for k, v in data.items() if isinstance(v, list) and now - v[1] < self.MAX_AGE}
        except (OSError, ValueError, TypeError, IndexError):
            self.data = {}

    @staticmethod
    def key(local: str, size: int, mtime: str) -> str:
        return hashlib.sha1(f"{os.path.normcase(local)}|{size}|{mtime}".encode("utf-8")).hexdigest()

    def get(self, key: str) -> str:
        with self._lock:
            v = self.data.get(key)
            return v[0] if v else ""

    def put(self, key: str, uri: str) -> None:
        with self._lock:
            self.data[key] = [uri, time.time()]
            self._save()

    def drop(self, key: str) -> None:
        with self._lock:
            if self.data.pop(key, None) is not None:
                self._save()

    def _save(self) -> None:
        try:
            with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


class DailyCounter:
    """Lo subido por una cuenta en las últimas 24 h (para reconocer el límite diario de Google)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as fh:
                self.items = [x for x in json.load(fh) if isinstance(x, list) and len(x) == 2]
        except (OSError, ValueError, TypeError):
            self.items = []
        self._dirty = 0

    def add(self, n: int) -> None:
        with self._lock:
            minute = int(time.time() // 60) * 60
            if self.items and self.items[-1][0] == minute:
                self.items[-1][1] += n
            else:
                self.items.append([minute, n])
            self._dirty += 1
            if self._dirty >= 50:
                self._save()

    def total(self) -> int:
        cut = time.time() - 86400
        with self._lock:
            self.items = [x for x in self.items if x[0] >= cut]
            return sum(x[1] for x in self.items)

    def near(self) -> bool:
        return self.total() >= DAILY_NEAR

    def save(self) -> None:
        with self._lock:
            self._save()

    def _save(self) -> None:
        self._dirty = 0
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.items, fh)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


# ── lo que hay en Drive: índice local ───────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS dirs(key TEXT PRIMARY KEY, path TEXT, id TEXT);
CREATE TABLE IF NOT EXISTS files(dir TEXT, key TEXT, name TEXT, size INTEGER, mtime INTEGER, md5 TEXT, id TEXT,
                                 PRIMARY KEY(dir, key));
CREATE TABLE IF NOT EXISTS versions(stamp TEXT, rel TEXT, size INTEGER, mtime INTEGER, md5 TEXT, id TEXT,
                                    PRIMARY KEY(stamp, rel));
CREATE TABLE IF NOT EXISTS vdirs(stamp TEXT, key TEXT, id TEXT, PRIMARY KEY(stamp, key));
"""


class DriveIndex:
    """Lo subido a la carpeta de Drive de un destino (carpetas y archivos con su id) y lo apartado a
    versiones. Se guarda al momento (cada subida queda apuntada), así una copia cortada no deja nada
    subido sin apuntar; si aun así se cortó a mitad, la próxima vez se relee Drive entero."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.executescript(_SCHEMA)
        self.meta: dict[str, str] = {}
        self.dirs: dict[str, list] = {}           # clave de carpeta → [ruta, id]
        self.files: dict[str, dict] = {}          # clave de carpeta → {clave de nombre: [nombre, tamaño, fecha, md5, id]}
        self._ops = 0
        self._last = time.monotonic()

    def load(self) -> None:
        with self.lock:
            self.meta = dict(self.con.execute("SELECT k, v FROM meta"))
            self.dirs = {k: [p, i] for k, p, i in self.con.execute("SELECT key, path, id FROM dirs")}
            self.files = {}
            for d, k, name, size, mtime, md5, fid in self.con.execute(
                    "SELECT dir, key, name, size, mtime, md5, id FROM files"):
                self.files.setdefault(d, {})[k] = [name, size, mtime, md5, fid]

    def set_meta(self, k: str, v) -> None:
        with self.lock:
            self.meta[k] = str(v)
            self.con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, str(v)))
            self._touch()

    def put_dir(self, rel: str, fid: str) -> None:
        key = norm_rel(rel)
        with self.lock:
            self.dirs[key] = [rel, fid]
            self.files.setdefault(key, {})
            self.con.execute("INSERT OR REPLACE INTO dirs VALUES (?, ?, ?)", (key, rel, fid))
            self._touch()

    def del_dir(self, key: str) -> None:
        with self.lock:
            self.dirs.pop(key, None)
            self.files.pop(key, None)
            self.con.execute("DELETE FROM dirs WHERE key = ?", (key,))
            self._touch()

    def put_file(self, rel_dir: str, name: str, size: int, mtime: int, md5: str, fid: str) -> None:
        d = norm_rel(rel_dir)
        with self.lock:
            self.files.setdefault(d, {})[fold(name)] = [name, size, mtime, md5, fid]
            self.con.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (d, fold(name), name, size, mtime, md5, fid))
            self._touch()

    def get_file(self, rel_dir: str, name: str) -> list | None:
        with self.lock:
            return (self.files.get(norm_rel(rel_dir)) or {}).get(fold(name))

    def del_file(self, rel_dir: str, name: str) -> None:
        d = norm_rel(rel_dir)
        with self.lock:
            (self.files.get(d) or {}).pop(fold(name), None)
            self.con.execute("DELETE FROM files WHERE dir = ? AND key = ?", (d, fold(name)))
            self._touch()

    def add_version(self, stamp: str, rel: str, size: int, mtime: int, md5: str, fid: str) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO versions VALUES (?, ?, ?, ?, ?, ?)",
                             (stamp, rel, size, mtime, md5, fid))
            self._touch()

    def stamps(self) -> list[str]:
        with self.lock:
            return sorted({r[0] for r in self.con.execute("SELECT stamp FROM versions")}
                          | {r[0] for r in self.con.execute("SELECT stamp FROM vdirs")})

    def stamp_size(self, stamp: str) -> int:
        with self.lock:
            return int(self.con.execute("SELECT coalesce(sum(size), 0) FROM versions WHERE stamp = ?",
                                        (stamp,)).fetchone()[0])

    def del_stamp(self, stamp: str) -> None:
        with self.lock:
            self.con.execute("DELETE FROM versions WHERE stamp = ?", (stamp,))
            self.con.execute("DELETE FROM vdirs WHERE stamp = ?", (stamp,))
            self._touch()

    def put_vdir(self, stamp: str, key: str, fid: str) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO vdirs VALUES (?, ?, ?)", (stamp, key, fid))
            self._touch()

    def vdir(self, stamp: str, key: str) -> str:
        with self.lock:
            row = self.con.execute("SELECT id FROM vdirs WHERE stamp = ? AND key = ?", (stamp, key)).fetchone()
            return row[0] if row else ""

    def replace_all(self, dirs: list[tuple[str, str]], files: list[tuple]) -> None:
        """Lo leído de Drive entero pasa a ser el índice."""
        with self.lock:
            self.con.execute("DELETE FROM dirs")
            self.con.execute("DELETE FROM files")
            self.con.executemany("INSERT OR REPLACE INTO dirs VALUES (?, ?, ?)",
                                 [(norm_rel(rel), rel, fid) for rel, fid in dirs])
            self.con.executemany("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 [(norm_rel(d), fold(n), n, s, m, md5, fid) for d, n, s, m, md5, fid in files])
            self.con.commit()
            self.load()

    def _touch(self) -> None:
        self._ops += 1
        if self._ops >= 200 or time.monotonic() - self._last > 5:
            self.commit()

    def commit(self) -> None:
        with self.lock:
            try:
                self.con.commit()
            except sqlite3.Error:
                pass
            self._ops = 0
            self._last = time.monotonic()

    def close(self) -> None:
        with self.lock:
            if self.con is not None:
                self.commit()
                self.con.close()
                self.con = None


# ── la sincronización ───────────────────────────────────────────────────────

class DriveBackupJob(engine.BackupJob):
    """Una sincronización con Google Drive de destino. Misma interfaz que engine.BackupJob."""

    def __init__(self, src: str, dst: str, patterns, excluded, writer: Writer, data_dir: str,
                 mode: str = MODE_MIRROR, threads: int = UPLOAD_THREADS, keep_days: int = 30,
                 force_full: bool = False):
        local = meta_root(data_dir, dst)
        # el motor espera un destino en disco: se le da la carpeta local del destino y luego se cambia
        super().__init__(src, local, patterns, excluded, mode, threads, index_path=None, force_full=force_full,
                         keep_days=keep_days, meta_root=local, verify=True, scrub_seconds=0)
        engine._case_sensitive = not (engine.IS_WIN or engine.IS_MAC)  # Drive distingue mayúsculas
        parsed = parse_dest(dst)
        if parsed is None:
            raise ValueError(dst)
        self.dst = dst
        self.account, self.machine, self.folder = parsed
        self.key = dest_key(dst)
        self.writer = writer
        self.threads = max(1, min(int(threads), UPLOAD_THREADS))
        self.network = True
        self.tolerance_ns = 10**6  # se guarda la fecha exacta del original (appProperties); Drive: ms
        self.fat = False
        self.dst_is_root = False
        self.ix = DriveIndex(index_path(data_dir, dst))
        self.sessions = Sessions(os.path.join(local, "subidas.json"))
        self.daily = DailyCounter(os.path.join(data_dir, "estado", f"gdrive-subido-{gdrive.safe_part(self.account)}.json"))
        self.dest_free: int | None = None     # libre en Drive (al analizar); None = sin límite o sin saber
        self.dest_total: int | None = None
        self.dest_folder_id = ""
        self._abort: DriveError | None = None
        self._dupes: list[tuple[str, str]] = []   # (ruta, id) repetidos en Drive: se apartan
        self._kids: dict[str, dict[str, str]] = {}
        self._vids: dict[tuple, str] = {}
        self._folder_lock = threading.Lock()
        self._online_at = 0.0
        self._host = urllib.parse.urlsplit(writer.api)

    # ── control ──
    def _index_signature(self) -> str:
        return json.dumps([os.path.normcase(self.src), self.key, self.patterns, sorted(self.excluded), self.mode])

    def run(self, copy: bool = True) -> None:
        try:
            self.held = False
            if self.phase != "ready":
                if not self.scan():
                    self.phase = "cancelled"
                    return
            if not copy:
                self.phase = "ready"
                return
            if not self.allow_mass_delete and self.mass_deletion():
                self.held = True
                self.phase = "ready"
                return
            self.copy()
        except DriveError as exc:
            if exc.kind == "cancelled" or self._cancel.is_set():
                self.phase = "cancelled"
            else:
                self.fatal = error_text(exc)
                self.phase = "failed"
        except Exception as exc:  # noqa: BLE001 - se muestra en la UI
            self.fatal = f"{type(exc).__name__}: {exc}"
            self.phase = "failed"
        finally:
            if self.phase != "ready":
                self.daily.save()
                self.ix.close()

    def discard(self) -> None:
        self.ix.close()

    def _dst_online(self) -> bool:
        """¿Hay conexión con Google? (un intento de conexión de 3 s, como mucho cada 2 s)"""
        if time.monotonic() - self._online_at < 2:
            return True
        port = self._host.port or (443 if self._host.scheme == "https" else 80)
        try:
            with socket.create_connection((self._host.hostname, port), timeout=3):
                self._online_at = time.monotonic()
                return True
        except OSError:
            return False

    def _call(self, fn, *a, **kw):
        """Una operación en Drive. Sin conexión espera a que vuelva (como al perder el disco)."""
        for attempt in range(6):
            try:
                return fn(*a, stop=self._cancel, **kw)
            except DriveError as exc:
                if exc.kind != "unreachable" or self._cancel.is_set():
                    raise
                if self._await_disk():
                    continue
                if attempt < 5:
                    self._cancel.wait(2 ** attempt)
                    continue
                raise

    def _fail(self, exc: DriveError) -> None:
        """Error que para toda la copia (Drive lleno, sin permiso, límite diario)."""
        if self._abort is None:
            self._abort = exc
        self._cancel.set()
        self._resume.set()

    # ── fase 1: lo que hay en Drive ──
    def _open_index(self) -> None:
        s = self.stats
        s.fast = False
        self._writer = self._reader = None
        self.ix.load()
        meta = self.ix.meta
        last_full = float(meta.get("last_full") or 0)
        if self.force_full:
            note = _("Revisión completa de la carpeta en Google Drive (pedida a mano)")
        elif not meta.get("dest_id"):
            note = _("Revisión completa de la carpeta en Google Drive (primera vez)")
        elif meta.get("signature") != self._index_signature():
            note = _("Revisión completa de la carpeta en Google Drive (cambió la configuración)")
        elif meta.get("clean") != "1":
            note = _("Revisión completa de la carpeta en Google Drive (la última copia no terminó)")
        elif time.time() - last_full > FULL_CHECK_DAYS * 86400:
            note = _("Revisión completa de la carpeta en Google Drive (la última fue hace más de {days} "
                     "días)").format(days=FULL_CHECK_DAYS)
        else:
            note = ""
        if note:
            self._remote_listing()
            last_full = time.time()
        else:
            s.fast = True
            days = (time.time() - last_full) / 86400
            note = _n("Análisis rápido con el índice de lo subido · revisión completa de Drive hace {n} día",
                      "Análisis rápido con el índice de lo subido · revisión completa de Drive hace {n} días",
                      round(days)).format(n=round(days))
        self.index_note = note
        self._last_full = last_full
        self.dest_folder_id = self.ix.meta.get("dest_id", "")
        kids: dict[str, dict[str, str]] = {}
        for key, (rel, _fid) in self.ix.dirs.items():
            if key:
                kids.setdefault(norm_rel(os.path.dirname(rel)), {})[fold(os.path.basename(rel))] = os.path.basename(rel)
        self._kids = kids
        try:
            limit, usage = self.writer.quota()
            self.dest_total = limit
            self.dest_free = max(0, limit - usage) if limit else None
        except DriveError:
            self.dest_free = self.dest_total = None

    def _find_root(self, create: bool, verify: bool = False, stop=None) -> str:
        """Las carpetas fijas: «DEV Backup Studio» → <equipo> → <carpeta>. Devuelve la del destino
        (o "" si no existe y no se pide crearla)."""
        meta = self.ix.meta
        dest = meta.get("dest_id", "")
        if dest and meta.get("dest_ok") == "1":
            if verify and not self.writer.alive(dest, stop=self._cancel):
                # borrada o en la papelera desde la web: el índice ya no vale; la próxima vez, todo de nuevo
                self.ix.set_meta("dest_id", "")
                self.ix.set_meta("dest_ok", "0")
                self.ix.replace_all([], [])
                raise DriveError("gone")
            return dest
        if dest and not self.writer.alive(dest, stop=self._cancel):
            dest = ""  # borrada o en la papelera desde la web: se empieza de nuevo
        if not dest:
            found = self.writer.find_folders(PROP_DESTROOT, self.key, stop=self._cancel)
            dest = found[0]["id"] if found else ""
        if not dest and create:
            root = self._root_id(create=True)
            machines = self.writer.find_folders(PROP_ROOT, "machine:" + self.machine.lower(), parent=root,
                                                stop=self._cancel)
            machine = machines[0]["id"] if machines else self.writer.create_folder(
                self.machine, root, {PROP_ROOT: "machine:" + self.machine.lower()}, stop=self._cancel)
            dest = self.writer.create_folder(self.folder, machine, {PROP_DESTROOT: self.key}, stop=self._cancel)
        if dest:
            self.ix.set_meta("dest_id", dest)
            self.ix.set_meta("dest_ok", "1" if create or self.ix.meta.get("dest_ok") == "1" else "0")
        return dest

    def _root_id(self, create: bool) -> str:
        """«DEV Backup Studio» en Mi unidad (una por cuenta; si hay dos, la más antigua)."""
        rid = self.ix.meta.get("root_id", "")
        if rid:
            return rid
        found = self.writer.find_folders(PROP_ROOT, "root", stop=self._cancel)
        if found:
            rid = found[0]["id"]
        elif create:
            rid = self.writer.create_folder(ROOT_NAME, "", {PROP_ROOT: "root"}, stop=self._cancel)
        if rid:
            self.ix.set_meta("root_id", rid)
        return rid

    def _remote_listing(self) -> None:
        """Lee de Drive todo lo que la app subió a este destino y rehace el índice."""
        self.ix.set_meta("dest_ok", "0")
        dest = self._find_root(create=False)
        if not dest:
            self.ix.replace_all([], [])
            return
        query = f"appProperties has {{ key='{PROP_DEST}' and value='{self.key}' }} and trashed = false"
        items = self.writer.list_all(query, FIELDS_LIST, stop=self._cancel)
        by_id = {it["id"]: it for it in items}
        rel_of: dict[str, str | None] = {dest: ""}

        def path(fid: str, depth: int = 0) -> str | None:
            if fid in rel_of:
                return rel_of[fid]
            it = by_id.get(fid)
            if it is None or depth > 200 or it.get("mimeType") != gdrive.FOLDER:
                return None
            parent = next((p for p in it.get("parents") or [] if p == dest or p in by_id), None)
            base = path(parent, depth + 1) if parent else None
            rel_of[fid] = None if base is None else (os.path.join(base, it["name"]) if base else it["name"])
            return rel_of[fid]

        dirs: dict[str, tuple[str, str]] = {"": ("", dest)}
        for fid, it in sorted(by_id.items(), key=lambda kv: kv[1].get("modifiedTime", "")):
            if it.get("mimeType") != gdrive.FOLDER:
                continue
            rel = path(fid)
            if rel is None or not rel:
                continue
            key = norm_rel(rel)
            if key in dirs and dirs[key][1] != fid:
                self._dupes.append((rel, fid))   # dos carpetas con el mismo nombre: la segunda se aparta
                rel_of[fid] = None
                continue
            dirs[key] = (rel, fid)
        files: dict[tuple[str, str], tuple] = {}
        for fid, it in sorted(by_id.items(), key=lambda kv: kv[1].get("modifiedTime", ""), reverse=True):
            if it.get("mimeType") == gdrive.FOLDER:
                continue
            parent = next((p for p in it.get("parents") or [] if rel_of.get(p) is not None), None)
            if parent is None:
                continue
            rel_dir = rel_of[parent]
            k = (norm_rel(rel_dir), fold(it["name"]))
            if k in files:
                self._dupes.append((os.path.join(rel_dir, it["name"]) if rel_dir else it["name"], fid))
                continue
            files[k] = (rel_dir, it["name"], int(it.get("size") or 0), _mtime_ns(it), it.get("md5Checksum") or "", fid)
        self.ix.replace_all(list(dirs.values()), list(files.values()))
        self.ix.set_meta("dest_id", dest)
        self.ix.set_meta("dest_ok", "1")

    def _dest_state(self, rel: str, ddir: str):
        key = norm_rel(rel)
        if key not in self.ix.dirs:
            return None
        files = {k: (v[0], v[1], v[2]) for k, v in (self.ix.files.get(key) or {}).items()}
        return files, dict(self._kids.get(key, {}))

    # ── fase 2: subir ──
    def _rel(self, path: str, base: str) -> str:
        return path[len(base):].lstrip("\\/")

    def copy(self) -> None:
        s = self.stats
        self.phase = "copying"
        s.copy_start = time.perf_counter()
        self._started_at = datetime.now()
        self._stamp = self._started_at.strftime(STAMP_FMT)
        self.disk_id = ""
        self.estimator = engine.Estimator((engine.last_history(self.meta_root) or {}).get("modelo"), network=True)
        threading.Thread(target=self._measure, name="medidor", daemon=True).start()
        self.ix.set_meta("clean", "0")
        self.ix.commit()
        clean = False
        try:
            self.dest_folder_id = self._call(self._find_root, create=True, verify=True)
            self.ix.put_dir("", self.dest_folder_id)
            self._copy_steps()
            clean = True
        except DriveError as exc:
            if not (exc.kind == "cancelled" and self._cancel.is_set() and self._abort is None):
                self._fail(exc)
        finally:
            s.step = "índice"
            self._commit_index(clean)
        sync_cancelled = self._cancel.is_set()
        s.copy_end = time.perf_counter()
        if self._abort is not None:
            self.fatal = error_text(self._abort)
            self.phase = "failed"
        else:
            self.phase = "cancelled" if sync_cancelled else "done"
        self._write_history(self.meta_root)

    def _copy_steps(self) -> None:
        s = self.stats
        s.step = "limpieza"
        for rel, fid in self._dupes:
            if self._cancel.is_set():
                return
            try:
                rel_dir, name = os.path.split(rel)
                self._call(self._to_versions, rel_dir, [name, 0, 0, "", fid], known_parent="")
            except DriveError as exc:
                self._error(rel, OSError(0, error_text(exc)))
        self._dupes = []

        s.step = "carpetas"
        rels = sorted((self._rel(d, self.dst) for d in self.dirs), key=lambda r: r.count(os.sep))
        for rel in rels:
            if self._cancel.is_set():
                return
            if not rel or norm_rel(rel) in self.ix.dirs:
                continue
            try:
                self._call(self._make_dir, rel)
                s.dirs_created += 1
            except DriveError as exc:
                self._error(os.path.join(self.dst, rel), OSError(0, error_text(exc)))

        s.step = "moviendo"
        t_moves = time.perf_counter()
        for old, new in self.case_renames:
            if self._cancel.is_set():
                return
            try:
                self._call(self._case_rename, self._rel(old, self.dst), self._rel(new, self.dst))
                s.case_done += 1
            except DriveError as exc:
                self._error(old, OSError(0, error_text(exc)))
        self._move_ok = bytearray(len(self.moves))
        for k, (old, item) in enumerate(self.moves):
            if self._cancel.is_set():
                return
            self._wait_if_paused()
            try:
                self._call(self._move_file, self._rel(old, self.dst), self._rel(item[0], self.src), item)
                self._move_ok[k] = 1
                s.moves_done += 1
            except DriveError as exc:
                if exc.kind in ("space", "auth", "denied", "noclient"):
                    raise
                # si no se puede mover, se sube el nuevo y el viejo pasa a versiones
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
        self.estimator.learn_renames(time.perf_counter() - t_moves, s.moves_done)
        if not self._cancel.is_set() and self.copies:
            self._make_room()
        self._copy_ok = bytearray(len(self.copies))
        self._copy_hash = [None] * len(self.copies)
        self._delete_ok = bytearray(len(self.deletes))

        if not self._cancel.is_set():
            s.step = "copiando"
            self._next = 0
            self._pool(self._copy_worker)

        if self.mode == MODE_MIRROR and not self._cancel.is_set():
            s.step = "versionando"
            self._next = 0
            t_del = time.perf_counter()
            self._pool(self._delete_worker)
            self.estimator.learn_renames(time.perf_counter() - t_del, s.deletes_done)
            for d in self.orphan_dirs:  # las más profundas primero
                if self._cancel.is_set():
                    break
                key = norm_rel(self._rel(d, self.dst))
                if not key or key not in self.ix.dirs or self._dir_used(key):
                    continue
                try:
                    self._call(self.writer.delete, self.ix.dirs[key][1])
                    self.ix.del_dir(key)
                    self._removed_dirs.add(d)
                    s.dirs_removed += 1
                except DriveError:
                    pass

        if not self._cancel.is_set() and self.keep_days > 0:
            s.step = "purgando"
            self._purge_versions(self.meta_root)

    def _dir_used(self, key: str) -> bool:
        """¿Queda algo en el índice dentro de esa carpeta? (lo excluido no se borra)"""
        if self.ix.files.get(key):
            return True
        prefix = key + os.sep
        return any(k.startswith(prefix) for k in self.ix.dirs)

    def _dir_id(self, rel_dir: str) -> str:
        entry = self.ix.dirs.get(norm_rel(rel_dir))
        return entry[1] if entry else self._make_dir(rel_dir)

    def _make_dir(self, rel: str, stop=None) -> str:
        """La carpeta de la copia para «rel» (y las que falten por encima). Una a la vez: sin repetidas."""
        with self._folder_lock:
            return self._make_dir_locked(rel)

    def _make_dir_locked(self, rel: str) -> str:
        if not rel:
            return self.dest_folder_id
        entry = self.ix.dirs.get(norm_rel(rel))
        if entry:
            return entry[1]
        pid = self._make_dir_locked(os.path.dirname(rel))
        fid = self.writer.create_folder(os.path.basename(rel), pid, {PROP_DEST: self.key}, stop=self._cancel)
        self.ix.put_dir(rel, fid)
        return fid

    def _case_rename(self, old_rel: str, new_rel: str, stop=None) -> None:
        key = norm_rel(new_rel)
        name = os.path.basename(new_rel)
        if key in self.ix.dirs:
            self.writer.move(self.ix.dirs[key][1], name=name, stop=self._cancel)
            prefix = self.ix.dirs[key][0]
            for k, (rel, fid) in list(self.ix.dirs.items()):  # las rutas guardadas, con las mayúsculas nuevas
                if k == key or k.startswith(key + os.sep):
                    self.ix.put_dir(new_rel + rel[len(prefix):], fid)
            return
        rel_dir = os.path.dirname(new_rel)
        entry = self.ix.get_file(rel_dir, name)
        if entry is None:
            raise DriveError("notfound", old_rel)
        self.writer.move(entry[4], name=name, stop=self._cancel)
        self.ix.put_file(rel_dir, name, entry[1], entry[2], entry[3], entry[4])

    def _move_file(self, old_rel: str, new_rel: str, item: tuple, stop=None) -> None:
        old_dir, old_name = os.path.split(old_rel)
        new_dir, new_name = os.path.split(new_rel)
        entry = self.ix.get_file(old_dir, old_name)
        if entry is None:
            raise DriveError("notfound", old_rel)
        old_parent = self._dir_id(old_dir)
        new_parent = self._dir_id(new_dir)
        self.writer.move(entry[4], new_parent, old_parent, name=new_name if new_name != entry[0] else None,
                         stop=self._cancel)
        self.ix.del_file(old_dir, old_name)
        self.ix.put_file(new_dir, new_name, entry[1], entry[2], entry[3], entry[4])

    def _copy_file(self, src: str, dst: str, kind: str, callback, buf, slot, rb) -> bytes | None:
        rel = self._rel(src, self.src)
        rel_dir, name = os.path.split(rel)
        stats, lock = self.stats, self._lock
        st = os.stat(lp(src))
        size, mtime = st.st_size, st.st_mtime_ns

        def progress(done: int) -> None:
            delta = done - slot[2]
            if delta:
                slot[2] = done
                with lock:
                    stats.bytes_done += delta

        def wait() -> None:
            self._wait_if_paused()
            if self._cancel.is_set():
                raise DriveError("cancelled")

        for attempt in range(4):
            try:
                parent = self._dir_id(rel_dir)
                old = self.ix.get_file(rel_dir, name)
                res = self.writer.upload(src, name, parent, mtime, {PROP_DEST: self.key, PROP_MTIME: str(mtime)},
                                         progress, self._cancel, wait, self.sessions)
                break
            except DriveError as exc:
                with lock:
                    stats.bytes_done -= slot[2]
                slot[2] = 0
                if exc.kind == "cancelled":
                    raise engine._Cancelled from exc
                if exc.kind == "unreachable":
                    if attempt < 3 and self._dst_online():
                        self._cancel.wait(2 ** attempt)
                        continue
                    raise ConnectionError(0, error_text(exc)) from exc
                if exc.kind == "quota" and self.daily.near():
                    exc = DriveError("daily", exc.detail)
                if exc.kind in ("space", "auth", "denied", "noclient", "daily"):
                    self._fail(exc)
                    raise engine._Cancelled from exc
                raise OSError(0, error_text(exc)) from exc
        self.daily.add(size)
        if old is not None and old[4] != res["id"]:
            try:
                self._call(self._to_versions, rel_dir, old, known_parent=parent)
            except DriveError as exc:
                self._error(dst, OSError(0, _("no se pudo apartar la versión anterior en Drive: {e}").format(
                    e=error_text(exc))))
        self.ix.put_file(rel_dir, name, size, mtime, res.get("md5Checksum") or "", res["id"])
        if res.get("md5Checksum"):
            with lock:
                stats.verified_copies += 1
        return None

    def _archive(self, dst_path: str) -> None:
        rel = self._rel(dst_path, self.dst)
        rel_dir, name = os.path.split(rel)
        entry = self.ix.get_file(rel_dir, name)
        if entry is None:
            raise FileNotFoundError(dst_path)
        try:
            self._call(self._to_versions, rel_dir, entry)
        except DriveError as exc:
            if exc.kind == "notfound":
                self.ix.del_file(rel_dir, name)
                raise FileNotFoundError(dst_path) from exc
            if exc.kind == "cancelled":
                raise ConnectionError(0, error_text(exc)) from exc
            if exc.kind in ("space", "auth", "denied", "noclient"):
                self._fail(exc)
            raise OSError(0, error_text(exc)) from exc
        self.ix.del_file(rel_dir, name)

    def _to_versions(self, rel_dir: str, entry: list, known_parent: str | None = None, stop=None) -> None:
        """Aparta un archivo de la copia a .versiones/<fecha>/<equipo>/<carpeta>/<ruta> (en el servidor)."""
        name, size, mtime, md5, fid = entry
        vparent = self._vdir(rel_dir)
        old_parent = known_parent if known_parent is not None else self.ix.dirs.get(norm_rel(rel_dir), ["", ""])[1]
        if not old_parent:
            parents = self.writer.parents(fid, stop=self._cancel)
            old_parent = ",".join(parents)
        self.writer.move(fid, vparent, old_parent, props={PROP_DEST: None, PROP_VDEST: self.key}, stop=self._cancel)
        rel = os.path.join(rel_dir, name) if rel_dir else name
        self.ix.add_version(self._stamp, rel, size, mtime, md5, fid)
        with self._lock:
            self.stats.versions_saved += 1

    def _vdir(self, rel_dir: str) -> str:
        """La carpeta de versiones de esta copia para «rel_dir» (se crea la primera vez)."""
        cached = self._vids.get(("d", norm_rel(rel_dir)))
        if cached:
            return cached
        with self._folder_lock:
            return self._vdir_locked(rel_dir)

    def _vdir_locked(self, rel_dir: str) -> str:
        key = norm_rel(rel_dir)
        if ("d", key) in self._vids:
            return self._vids[("d", key)]
        if not rel_dir:
            fid = self._vbase()
        else:
            pid = self._vdir_locked(os.path.dirname(rel_dir))
            fid = self.writer.create_folder(os.path.basename(rel_dir), pid, {PROP_VDEST: self.key}, stop=self._cancel)
        self._vids[("d", key)] = fid
        return fid

    def _vbase(self) -> str:
        """.versiones/<fecha>/<equipo>/<carpeta> de esta sincronización."""
        existing = self.ix.vdir(self._stamp, "")
        if existing:
            return existing
        root = self._root_id(create=True)
        vroot = self.ix.meta.get("vroot_id", "")
        if not vroot:
            found = self.writer.find_folders(PROP_ROOT, "versions", parent=root, stop=self._cancel)
            vroot = found[0]["id"] if found else self.writer.create_folder(VERSIONS_NAME, root, {PROP_ROOT: "versions"},
                                                                          stop=self._cancel)
            self.ix.set_meta("vroot_id", vroot)
        found = self.writer.find_folders(PROP_ROOT, "stamp", parent=vroot, name=self._stamp, stop=self._cancel)
        stamp = found[0]["id"] if found else self.writer.create_folder(self._stamp, vroot, {PROP_ROOT: "stamp"},
                                                                       stop=self._cancel)
        machine = self.writer.create_folder(self.machine, stamp, {PROP_VDEST: self.key}, stop=self._cancel)
        base = self.writer.create_folder(self.folder, machine, {PROP_VDEST: self.key}, stop=self._cancel)
        self.ix.put_vdir(self._stamp, "@stamp", stamp)
        self.ix.put_vdir(self._stamp, "@machine", machine)
        self.ix.put_vdir(self._stamp, "", base)
        return base

    def _make_room(self) -> None:
        """Si lo que hay que subir no cabe en Drive, quita primero las versiones más antiguas de esta copia."""
        need = sum(c[1] for c in self.copies) + engine.SPACE_RESERVE
        try:
            limit, usage = self._call(lambda stop=None: self.writer.quota())
        except DriveError:
            return
        if limit is None or limit - usage >= need:
            return
        s = self.stats
        s.step = "haciendo sitio"
        freed = 0
        for stamp in self.ix.stamps():
            if self._cancel.is_set() or limit - usage + freed >= need:
                break
            if stamp == self._stamp:
                continue
            size = self.ix.stamp_size(stamp)
            try:
                self._remove_stamp(stamp)
            except DriveError as exc:
                self._error(stamp, OSError(0, error_text(exc)))
                continue
            freed += size
            s.versions_purged += 1
        s.space_freed += freed

    def _purge_versions(self, root: str) -> None:
        limit = datetime.now() - timedelta(days=self.keep_days)
        for stamp in self.ix.stamps():
            try:
                when = datetime.strptime(stamp, STAMP_FMT)
            except ValueError:
                continue
            if when >= limit or self._cancel.is_set():
                continue
            try:
                self._remove_stamp(stamp)
                self.stats.versions_purged += 1
            except DriveError as exc:
                self._error(stamp, OSError(0, error_text(exc)))

    def _remove_stamp(self, stamp: str) -> None:
        remove_stamp(self.writer, self.ix, stamp, self._cancel)

    def _commit_index(self, clean: bool = True) -> None:
        self.ix.set_meta("signature", self._index_signature())
        self.ix.set_meta("last_full", self._last_full)
        self.ix.set_meta("updated", time.time())
        self.ix.set_meta("clean", "1" if clean else "0")
        self.ix.commit()


def remove_stamp(writer: Writer, ix: DriveIndex, stamp: str, stop: threading.Event | None = None) -> None:
    """Quita para siempre las versiones de esta copia en esa fecha (y la fecha, si se queda vacía)."""
    for key in ("@machine", ""):
        fid = ix.vdir(stamp, key)
        if fid:
            writer.delete(fid, stop=stop)
            break
    stamp_id = ix.vdir(stamp, "@stamp")
    if stamp_id:
        try:
            left = writer.list_all(f"'{stamp_id}' in parents and trashed = false", "id", stop)
            if not left:
                writer.delete(stamp_id, stop=stop)
        except DriveError:
            pass
    ix.del_stamp(stamp)
    ix.commit()


# ── Recuperar ───────────────────────────────────────────────────────────────

def load_snapshots(ix_file: str) -> list[versions.Snapshot]:
    """Fechas con versiones de este destino, de la más reciente a la más antigua (del índice local)."""
    if not os.path.exists(ix_file):
        return []
    try:
        con = sqlite3.connect(f"file:{ix_file}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT stamp, rel, size, mtime FROM versions ORDER BY stamp DESC, rel").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    snaps: dict[str, versions.Snapshot] = {}
    for stamp, rel, size, mtime in rows:
        snap = snaps.get(stamp)
        if snap is None:
            try:
                when = datetime.strptime(stamp, STAMP_FMT)
            except ValueError:
                continue
            snap = snaps[stamp] = versions.Snapshot(when, stamp, VPREFIX + stamp, VPREFIX + stamp)
        snap.files.append((rel, int(size), int(mtime)))
    return sorted(snaps.values(), key=lambda s: s.stamp, reverse=True)


def version_entry(ix_file: str, stamp: str, rel: str) -> tuple[str, str] | None:
    """(id, md5) de un archivo apartado."""
    try:
        con = sqlite3.connect(f"file:{ix_file}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT id, md5 FROM versions WHERE stamp = ? AND rel = ?", (stamp, rel)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return (row[0], row[1] or "") if row else None


def stamp_folder(ix_file: str, stamp: str) -> str:
    try:
        con = sqlite3.connect(f"file:{ix_file}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT id FROM vdirs WHERE stamp = ? AND key = ''", (stamp,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return row[0] if row else ""


def fetch(writer: Writer, ix_file: str, stamp: str, rel: str, target: str,
          stop: threading.Event | None = None) -> None:
    """Baja una versión a «target» (entera o nada) y comprueba su MD5."""
    entry = version_entry(ix_file, stamp, rel)
    if entry is None:
        raise OSError(0, _("ya no está en Google Drive"))
    fid, md5 = entry
    try:
        got = writer.download(fid, target, stop)
    except DriveError as exc:
        if exc.kind in ("denied", "notfound"):
            raise OSError(0, _("ya no está en Google Drive")) from exc
        raise OSError(0, error_text(exc)) from exc
    if md5 and got != md5:
        try:
            os.remove(lp(target))
        except OSError:
            pass
        raise OSError(0, _("lo bajado de Google Drive no coincide con el original (MD5)"))


def restore(writer: Writer, ix_file: str, items: list[tuple[str, str, str, int, int]], when: datetime | None = None,
            cancel: threading.Event | None = None, progress=None) -> versions.RestoreResult:
    """Como versions.restore, bajando de Drive. «items»: (fecha, ruta en la copia, destino, tamaño, fecha_ns).
    Nunca sobrescribe: si ya hay otro archivo distinto, la versión se guarda al lado con otro nombre."""
    when = when or datetime.now()
    res = versions.RestoreResult()
    for i, (stamp, rel, target, size, mtime_ns) in enumerate(items):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(i, len(items), target)
        try:
            try:
                st = os.stat(lp(target))
            except FileNotFoundError:
                st = None
            renamed = False
            if st is not None:
                if os.path.isfile(lp(target)) and versions.same_file(st, size, mtime_ns):
                    res.same += 1
                    res.targets.append(target)
                    continue
                target = versions.recovered_name(target, when)
                renamed = True
            os.makedirs(lp(os.path.dirname(target)), exist_ok=True)
            tmp = target + ".dbsget"
            fetch(writer, ix_file, stamp, rel, tmp, cancel)
            try:
                os.utime(lp(tmp), ns=(mtime_ns, mtime_ns))
                if os.path.lexists(lp(target)):
                    target = versions.recovered_name(target, when)
                    renamed = True
                os.replace(lp(tmp), lp(target))
            except BaseException:
                try:
                    os.remove(lp(tmp))
                except OSError:
                    pass
                raise
            res.renamed += renamed
            res.restored += not renamed
            res.bytes += size
            res.targets.append(target)
        except OSError as exc:
            res.failed.append((target, exc.strerror or str(exc)))
        except DriveError as exc:
            if exc.kind == "cancelled":
                break
            res.failed.append((target, error_text(exc)))
    if progress:
        progress(len(items), len(items), "")
    return res


def delete_snapshot(writer: Writer, ix_file: str, stamp: str) -> None:
    ix = DriveIndex(ix_file)
    try:
        ix.load()
        remove_stamp(writer, ix, stamp)
    finally:
        ix.close()
