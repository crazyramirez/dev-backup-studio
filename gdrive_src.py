"""Google Drive como origen de la sincronización: copiar tu Drive a un disco o una carpeta.

El origen es «gdrive:/<cuenta>» y se ve como una carpeta con tres partes: «Mi unidad», «Unidades
compartidas/<unidad>» y «Compartido conmigo». Lo que se copia se elige en el árbol de la pestaña
Carpetas, con casillas, como en cualquier origen (y las reglas, p. ej. node_modules, también valen).

- El análisis es el del motor (engine.BackupJob): solo cambia de dónde sale «lo que hay en el
  origen». La primera vez se pide a Google la lista de todo el Drive de golpe (de 1000 en 1000, no
  carpeta por carpeta) y se guarda en el equipo (DriveCache); después, cada análisis solo pide lo que
  ha cambiado desde la vez anterior (el registro de cambios de Drive: unas pocas peticiones). Una
  vez por semana se vuelve a pedir la lista entera, por si acaso.
- Copiar es bajar: lo nuevo o cambiado, comprobado con el MD5 de Google; la fecha del archivo en el
  disco es la de Drive (así la próxima vez se reconoce sin bajarlo). Los Documentos, Hojas y
  Presentaciones de Google se guardan como .docx, .xlsx y .pptx (y los dibujos como .png); se
  comparan por fecha, porque su tamaño no se sabe hasta exportarlos. Formularios, sitios, mapas y
  accesos directos no tienen archivo y no se copian.
- Lo demás es de la sincronización: versiones de lo borrado o cambiado en Drive, borrado masivo
  protegido, integridad del disco (lo dañado se vuelve a bajar) y, sin conexión, espera y sigue.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import json
import queue
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import engine
import gdrive
import gdrive_up
from engine import lp, match_rule, norm_rel
from gdrive import EXPORTS, FOLDER, SHORTCUT, DriveError
from i18n import _

ROOTS = [gdrive.MY_DRIVE, gdrive.SHARED_DRIVES, gdrive.SHARED_WITH_ME]
DEFAULT_EXCLUDED = {norm_rel(gdrive.SHARED_WITH_ME)}  # lo compartido contigo: solo si lo marcas
LIST_THREADS = 6
FULL_DAYS = 7      # la lista entera de Drive se vuelve a pedir cada semana
FIELDS_ALL = "id,name,mimeType,parents,size,md5Checksum,modifiedTime,sharedWithMeTime,driveId,trashed"
DOWNLOAD_THREADS = 6
SEP = re.compile(r"[\\/]+")


def is_src(path: str | None) -> bool:
    return gdrive_up.is_dest(path) and parse_src(path) is not None


def make_src(account_id: str) -> str:
    return os.path.normpath(f"{gdrive_up.PREFIX}/{account_id}")


def parse_src(path: str | None) -> str | None:
    """La cuenta de un origen «gdrive:/<cuenta>», o None."""
    if not gdrive_up.is_dest(path):
        return None
    parts = [p for p in SEP.split(str(path)[len(gdrive_up.PREFIX):]) if p]
    return parts[0] if len(parts) == 1 else None


def is_cloud(path: str | None) -> bool:
    """¿Es un origen o un destino de Google Drive?"""
    return gdrive_up.is_dest(path)


def mtime_ns(iso: str) -> int:
    """La fecha de Drive (RFC 3339, con milisegundos) en ns, exacta (sin pasar por float)."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?", str(iso or ""))
    if not m:
        return 0
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    frac = (m.group(2) or "0")[:9].ljust(9, "0")
    return int(base.timestamp()) * 10**9 + int(frac)


def error_text(exc: DriveError) -> str:
    if exc.kind == "auth":
        return _("Google no acepta la llave de esta cuenta: vuelve a elegir Google Drive como origen para "
                 "conectarla de nuevo.")
    if exc.kind == "denied":
        return _("Google no deja leer este Drive: vuelve a elegir Google Drive como origen y da el permiso de "
                 "lectura.")
    return gdrive_up.error_text(exc)


# ── lo que hay en Drive, guardado en el equipo ─────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, name TEXT, mime TEXT, parents TEXT, size TEXT, md5 TEXT,
                                 mtime TEXT, shared INTEGER, drive TEXT);
"""


class DriveCache:
    """Todas las carpetas y archivos de un Drive (Mi unidad, unidades compartidas y lo compartido
    contigo), guardados en el equipo. sync() lo pone al día: la primera vez (y cada semana) con la
    lista entera, de 1000 en 1000; las demás, solo con lo que ha cambiado desde la última vez."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.executescript(_SCHEMA)
        return con

    def meta(self) -> dict:
        try:
            con = self._con()
            try:
                return dict(con.execute("SELECT k, v FROM meta"))
            finally:
                con.close()
        except sqlite3.Error:
            return {}

    @staticmethod
    def _row(it: dict, drive: str = "") -> tuple:
        return (it["id"], it.get("name", ""), it.get("mimeType", ""), json.dumps(it.get("parents") or []),
                str(it.get("size") or ""), it.get("md5Checksum") or "", it.get("modifiedTime") or "",
                1 if it.get("sharedWithMeTime") else 0, it.get("driveId") or drive)

    def sync(self, drive: gdrive.Drive, stop: threading.Event | None = None, progress=None,
             force: bool = False) -> str:
        """Pone la lista al día. Devuelve «full» (se pidió entera) o «changes» (solo lo cambiado)."""
        meta = self.meta()
        full = force or not meta.get("token") or time.time() - float(meta.get("full_at") or 0) > FULL_DAYS * 86400
        drives = drive.shared_drives()
        con = self._con()
        try:
            if not full:
                try:
                    self._changes(con, drive, meta["token"], stop, progress)
                except DriveError as exc:
                    if exc.kind not in ("notfound", "denied", "server"):
                        raise
                    full = True  # el registro de cambios ya no sirve: se pide todo de nuevo
            if full:
                token = drive.get("/changes/startPageToken", {"supportsAllDrives": "true"}, stop)["startPageToken"]
                root = drive.get("/files/root", {"fields": "id"}, stop)["id"]
                con.execute("DELETE FROM items")
                self._list(con, drive, {}, "", stop, progress)
                for d in drives:
                    self._list(con, drive, {"corpora": "drive", "driveId": d["id"]}, d["id"], stop, progress)
                con.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                                [("token", token), ("root", root), ("full_at", str(time.time()))])
            else:
                known = {d["id"] for d in json.loads(meta.get("drives") or "[]")}
                for d in drives:  # unidades compartidas nuevas: se piden enteras
                    if d["id"] not in known:
                        self._list(con, drive, {"corpora": "drive", "driveId": d["id"]}, d["id"], stop, progress)
                now = {d["id"] for d in drives}
                for gone in known - now:   # ya no eres miembro: fuera de la lista
                    con.execute("DELETE FROM items WHERE drive = ?", (gone,))
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", ("drives", json.dumps(drives)))
            con.commit()
        finally:
            con.close()
        return "full" if full else "changes"

    def _list(self, con, drive: gdrive.Drive, extra: dict, drive_id: str, stop, progress) -> None:
        token, count = "", 0
        while True:
            params = {"q": "trashed = false", "pageSize": 1000, "fields": f"nextPageToken,files({FIELDS_ALL})",
                      **extra}
            if drive_id:
                params.update(supportsAllDrives="true", includeItemsFromAllDrives="true")
            if token:
                params["pageToken"] = token
            data = drive.get("/files", params, stop)
            rows = [self._row(it, drive_id) for it in data.get("files") or [] if not it.get("trashed")]
            con.executemany("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            count += len(rows)
            if progress:
                progress(count)
            token = data.get("nextPageToken") or ""
            if not token:
                return

    def _changes(self, con, drive: gdrive.Drive, token: str, stop, progress) -> None:
        count = 0
        while True:
            data = drive.get("/changes", {
                "pageToken": token, "pageSize": 1000, "includeRemoved": "true", "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "fields": f"nextPageToken,newStartPageToken,changes(changeType,fileId,removed,file({FIELDS_ALL}))"},
                stop)
            for ch in data.get("changes") or []:
                if ch.get("changeType") == "drive":
                    continue
                f = ch.get("file") or {}
                if ch.get("removed") or f.get("trashed") or not f:
                    con.execute("DELETE FROM items WHERE id = ?", (ch.get("fileId"),))
                else:
                    con.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", self._row(f))
                count += 1
            if progress:
                progress(count)
            if data.get("newStartPageToken"):
                con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", ("token", data["newStartPageToken"]))
                return
            token = data.get("nextPageToken") or ""
            if not token:
                return

    def snapshot(self) -> "Snapshot | None":
        """La lista en memoria (None si aún no se ha pedido nunca)."""
        meta = self.meta()
        if not meta.get("root"):
            return None
        con = self._con()
        try:
            rows = con.execute("SELECT id, name, mime, parents, size, md5, mtime, shared, drive FROM items").fetchall()
        finally:
            con.close()
        return Snapshot(meta["root"], json.loads(meta.get("drives") or "[]"), rows)


class Snapshot:
    """La lista de un Drive en memoria: los hijos de cada carpeta, como los da la API."""

    def __init__(self, root: str, drives: list[dict], rows: list[tuple]):
        self.root = root
        self.drives = drives
        self.kids: dict[str, list[dict]] = {}
        known = {r[0] for r in rows}
        self.shared: list[dict] = []
        for fid, name, mime, parents, size, md5, mtime, shared, drive in rows:
            it = {"id": fid, "name": name, "mimeType": mime, "modifiedTime": mtime}
            if size:
                it["size"] = size
            if md5:
                it["md5Checksum"] = md5
            ps = json.loads(parents or "[]")
            for p in ps:
                self.kids.setdefault(p, []).append(it)
            # lo compartido contigo cuya carpeta no ves: arriba del todo en «Compartido conmigo»
            if shared and not drive and not any(p in known or p == root for p in ps):
                self.shared.append(it)

    def children(self, fid: str) -> list[dict]:
        if fid == "@drives":
            return [{"id": d["id"], "name": d.get("name", ""), "mimeType": FOLDER, "_drive": d["id"]}
                    for d in self.drives]
        if fid == "@shared":
            return list(self.shared)
        return list(self.kids.get(self.root if fid == "root" else fid, []))


@dataclass
class Item:
    name: str
    id: str
    size: int      # 0 en lo exportado (no se sabe hasta exportarlo)
    md5: str
    mtime: int     # ns
    export: str = ""


class DriveSource:
    """Un Drive visto como carpeta: rutas «Mi unidad/…», «Unidades compartidas/<unidad>/…» y
    «Compartido conmigo/…». Recuerda el id de cada carpeta que ve (para el árbol y para el análisis)."""

    def __init__(self, drive: gdrive.Drive, snapshot: "Snapshot | None" = None, snapshot_loader=None):
        self.drive = drive
        self.snapshot = snapshot
        self._loader = snapshot_loader   # para el árbol: la lista guardada se lee al desplegar, en otro hilo
        self._lock = threading.Lock()
        self._dirs: dict[str, tuple[str, str]] = {
            norm_rel(gdrive.MY_DRIVE): ("root", ""),
            norm_rel(gdrive.SHARED_DRIVES): ("@drives", ""),
            norm_rel(gdrive.SHARED_WITH_ME): ("@shared", ""),
        }
        self.errors: list[tuple[str, str]] = []
        self.skipped = 0
        self.folders = 0

    def _children(self, fid: str, did: str, stop: threading.Event | None) -> list[dict]:
        if self.snapshot is None and self._loader is not None:
            with self._lock:
                if self._loader is not None:
                    try:
                        self.snapshot = self._loader()
                    except (sqlite3.Error, OSError, ValueError):
                        self.snapshot = None
                    self._loader = None
        if self.snapshot is not None:
            return self.snapshot.children(fid)  # de la lista guardada: sin esperar a Google
        if fid == "@drives":
            return [{"id": d["id"], "name": d.get("name", ""), "mimeType": FOLDER, "_drive": d["id"]}
                    for d in self.drive.shared_drives()]
        if fid == "@shared":
            return self.drive.shared_with_me(stop)
        return self.drive.children(fid, did, stop)

    def _register(self, rel: str, items: list[dict], did: str) -> dict[str, str]:
        names = gdrive.disk_names(items)
        with self._lock:
            for it in items:
                if it.get("mimeType") == FOLDER:
                    child = os.path.join(rel, names[it["id"]])
                    self._dirs[norm_rel(child)] = (it["id"], it.get("_drive") or did)
        return names

    def resolve(self, rel: str, stop: threading.Event | None = None) -> tuple[str, str]:
        """(id, unidad compartida) de una carpeta; lee las de encima si aún no las conoce."""
        key = norm_rel(rel)
        with self._lock:
            found = self._dirs.get(key)
        if found:
            return found
        parent = os.path.dirname(rel)
        if not parent:
            raise DriveError("notfound", rel)
        pid, pdid = self.resolve(parent, stop)
        self._register(parent, self._children(pid, pdid, stop), pdid)
        with self._lock:
            found = self._dirs.get(key)
        if not found:
            raise DriveError("notfound", rel)
        return found

    def subdirs(self, rel: str, stop: threading.Event | None = None) -> list[str]:
        """Las carpetas dentro de «rel» (para el árbol de Carpetas, al desplegar)."""
        if not rel:
            return list(ROOTS)
        fid, did = self.resolve(rel, stop)
        items = self._children(fid, did, stop)
        names = self._register(rel, items, did)
        return sorted((names[it["id"]] for it in items if it.get("mimeType") == FOLDER), key=str.lower)

    def walk(self, excluded: set[str], patterns: list[str], stop: threading.Event,
             progress=None) -> tuple[dict[str, dict], dict[str, Item]]:
        """Recorre lo marcado. Devuelve ({carpeta: {"dirs": [...], "files": [Item]}}, {ruta: Item})."""
        tree: dict[str, dict] = {"": {"dirs": list(ROOTS), "files": []}}
        items: dict[str, Item] = {}
        work: queue.Queue = queue.Queue()
        pending = [0]
        cond = threading.Condition()
        errors: list[DriveError] = []
        lock = threading.Lock()

        def skip(rel: str, name: str) -> bool:
            return norm_rel(rel) in excluded or bool(match_rule(name, patterns))

        for root in ROOTS:
            if not skip(root, root):
                work.put(root)

        def worker():
            while True:
                with cond:
                    while work.empty() and pending[0] > 0 and not errors and not stop.is_set():
                        cond.wait(0.2)
                    if work.empty() or errors or stop.is_set():
                        cond.notify_all()
                        return
                    rel = work.get()
                    pending[0] += 1
                try:
                    if progress:
                        progress(rel)
                    fid, did = self.resolve(rel, stop)
                    raw = self._children(fid, did, stop)
                    names = self._register(rel, raw, did)
                    node = {"dirs": [], "files": []}
                    for it in raw:
                        name = names[it["id"]]
                        child = os.path.join(rel, name)
                        mime = it.get("mimeType", "")
                        if mime == FOLDER:
                            node["dirs"].append(name)
                            if not skip(child, name):
                                work.put(child)
                            continue
                        if mime == SHORTCUT:
                            with lock:
                                self.skipped += 1
                            continue
                        export = ""
                        if mime.startswith("application/vnd.google-apps."):
                            if mime not in EXPORTS:
                                with lock:
                                    self.skipped += 1   # formularios, sitios, mapas…
                                continue
                            export = EXPORTS[mime][0]
                        size = int(it["size"]) if it.get("size") not in (None, "") and not export else 0
                        item = Item(name, it["id"], size, str(it.get("md5Checksum") or ""),
                                    mtime_ns(it.get("modifiedTime", "")), export)
                        node["files"].append(item)
                        with lock:
                            items[norm_rel(child)] = item
                    with lock:
                        tree[norm_rel(rel)] = node
                        self.folders += 1
                except DriveError as exc:
                    if exc.kind in ("denied", "notfound"):  # una carpeta que ya no se puede leer: se sigue
                        with lock:
                            self.errors.append((rel, exc.detail or exc.kind))
                    else:
                        errors.append(exc)
                finally:
                    with cond:
                        pending[0] -= 1
                        cond.notify_all()

        threads = [threading.Thread(target=worker, name=f"drive-listar-{i}", daemon=True)
                   for i in range(LIST_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        if stop.is_set():
            raise DriveError("cancelled")
        return tree, items


def changes_since(drive: gdrive.Drive, since: float, stop: threading.Event | None = None, pages: int = 5) -> dict:
    """Para los recordatorios: archivos de Drive cambiados desde «since» (de todo el Drive, sin abrir nada)."""
    iso = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    q = f"modifiedTime > '{iso}' and trashed = false and mimeType != '{FOLDER}'"
    files = size = 0
    token, complete, latest = "", True, ""
    for page in range(pages):
        params = {"q": q, "pageSize": 1000, "fields": "nextPageToken,files(size,modifiedTime)",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if token:
            params["pageToken"] = token
        data = drive.get("/files", params, stop)
        for f in data.get("files") or []:
            files += 1
            size += int(f.get("size") or 0)
            latest = max(latest, f.get("modifiedTime") or "")
        token = data.get("nextPageToken") or ""
        if not token:
            break
        if page == pages - 1:
            complete = False
    return {"files": files, "dirs": 0, "bytes": size, "complete": complete, "latest": latest}


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(lp(path), "rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class DriveSourceJob(engine.BackupJob):
    """Una sincronización con Google Drive de origen. Misma interfaz que engine.BackupJob."""

    def __init__(self, src: str, dst: str, patterns, excluded, drive: gdrive.Drive, *args,
                 cache_path: str | None = None, **kwargs):
        super().__init__(src, dst, patterns, excluded, *args, **kwargs)
        self.account = parse_src(src)
        self.drive = drive
        self.cache = DriveCache(cache_path) if cache_path else None
        self.listing = ""   # «full» o «changes»: cómo se puso al día la lista de Drive
        self.source = DriveSource(drive)
        self.threads = max(1, min(self.threads, DOWNLOAD_THREADS))
        self._tree: dict[str, dict] = {}
        self._items: dict[str, Item] = {}
        self._abort: DriveError | None = None

    def run(self, copy: bool = True) -> None:
        try:
            self.held = False
            if self.phase != "ready":
                if not self.scan():
                    self._close_index(commit=False)
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
            if self._abort is not None:
                self.fatal = error_text(self._abort)
                self.phase = "failed"
        except DriveError as exc:
            self._close_index(commit=False)
            if exc.kind == "cancelled" or self._cancel.is_set():
                self.phase = "cancelled"
            else:
                self.fatal = error_text(exc)
                self.phase = "failed"
        except Exception as exc:  # noqa: BLE001 - se muestra en la UI
            self.fatal = f"{type(exc).__name__}: {exc}"
            self._close_index(commit=False)
            self.phase = "failed"

    # ── lo que hay en Drive ──
    def scan(self) -> bool:
        s = self.stats
        self.phase = "scanning"
        start = s.scan_start = time.perf_counter()

        def progress(rel: str) -> None:
            s.current_dir = "Google Drive  ›  " + rel

        if self.cache is not None:
            first = not self.cache.meta().get("token") or self.force_full

            def counted(n: int) -> None:
                s.current_dir = "Google Drive  ·  " + (_("leyendo la lista entera: {n} elementos").format(n=n) if first
                                                     else _("{n} cambios desde la última vez").format(n=n))

            s.current_dir = "Google Drive  ·  " + (_("leyendo la lista entera de Drive (solo la primera vez)")
                                                 if first else _("pidiendo los cambios desde la última vez"))
            self.listing = self.cache.sync(self.drive, self._cancel, counted, force=self.force_full)
            self.source = DriveSource(self.drive, self.cache.snapshot())
        self._tree, self._items = self.source.walk(self.excluded, self.patterns, self._cancel, progress)
        for rel, msg in self.source.errors:
            self._error(os.path.join(self.src, rel), OSError(0, msg))
        ok = super().scan()
        s.scan_start = start  # el análisis incluye recorrer Drive
        if self.listing:
            self.index_note += "  ·  " + (_("Google Drive: lista entera (la primera vez y cada semana)")
                                          if self.listing == "full" else _("Google Drive: solo los cambios"))
        return ok

    def _src_entries(self, rel: str, sdir: str) -> list[tuple]:
        node = self._tree.get(norm_rel(rel))
        if node is None:
            raise FileNotFoundError(sdir)
        return ([(d, "dir", 0, 0, None) for d in node["dirs"]]
                + [(it.name, "file", it.size, it.mtime, None) for it in node["files"]])

    def _src_exists(self, rel: str) -> bool:
        key = norm_rel(rel)
        return key in self._tree or key in self._items

    def _src_isdir(self, rel: str) -> bool:
        return norm_rel(rel) in self._tree

    def _same_entry(self, rel: str, name: str, size: int, mtime: int, have: tuple) -> bool:
        it = self._items.get(norm_rel(os.path.join(rel, name) if rel else name))
        # Drive guarda la fecha en milisegundos: 1 ms de margen (lo que bajaron las versiones anteriores
        # lleva la fecha puesta con decimales y puede quedar a unos cientos de ns)
        near = abs(have[2] - mtime) <= max(self.tolerance_ns, 10**6)
        if it is not None and it.export:
            return near  # lo exportado no tiene tamaño en Drive: basta con la fecha
        return (have[1] == size and near) or self._same(size, mtime, have)

    def _dst_online(self) -> bool:
        return super()._dst_online() and gdrive_up.online(self.drive.api)

    # ── bajar ──
    def _item(self, src: str) -> tuple[str, Item]:
        rel = src[len(self.src):].lstrip("\\/")
        it = self._items.get(norm_rel(rel))
        if it is None:
            raise FileNotFoundError(src)
        return rel, it

    def _fetch(self, it: Item, tmp: str, slot) -> tuple[str, bytes, int]:
        """Baja (o exporta) un archivo a «tmp». Devuelve (md5, sha256, bytes)."""
        if it.export:
            path, params = f"/files/{it.id}/export", {"mimeType": it.export}
        else:
            path, params = f"/files/{it.id}", {"alt": "media", "supportsAllDrives": "true"}
        md5, sha, got = hashlib.md5(), hashlib.sha256(), 0
        stats, lock = self.stats, self._lock
        try:
            resp = self.drive.open(path, params, stop=self._cancel, timeout=120)
        except DriveError as exc:
            if exc.kind == "cancelled":
                raise engine._Cancelled from exc
            if exc.kind == "unreachable":
                raise ConnectionError(0, error_text(exc)) from exc
            if exc.kind == "auth":
                self._abort = exc
                self.cancel()
                raise engine._Cancelled from exc
            if "exportSizeLimit" in exc.detail:
                raise OSError(0, _("Google no deja exportar documentos de más de 10 MB")) from exc
            raise OSError(0, error_text(exc)) from exc
        try:
            with resp, open(lp(tmp), "wb") as out:
                while True:
                    self._wait_if_paused()
                    if self._cancel.is_set():
                        raise engine._Cancelled
                    block = resp.read(gdrive.READ_CHUNK)
                    if not block:
                        break
                    out.write(block)
                    md5.update(block)
                    sha.update(block)
                    got += len(block)
                    slot[2] += len(block)
                    with lock:
                        stats.bytes_done += len(block)
        except (http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            engine._remove_quiet(lp(tmp))
            raise ConnectionError(0, _("la descarga de Google Drive se cortó")) from exc
        except BaseException:
            engine._remove_quiet(lp(tmp))
            raise
        return md5.hexdigest(), sha.digest(), got

    def _copy_file(self, src: str, dst: str, kind: str, callback, buf, slot, rb) -> bytes | None:
        _rel, it = self._item(src)
        tmp = dst + engine.TMP_SUFFIX
        os.makedirs(lp(os.path.dirname(dst)), exist_ok=True)
        digest = None
        for attempt in (1, 2):
            for retry in range(4):
                try:
                    md5, digest, got = self._fetch(it, tmp, slot)
                    break
                except ConnectionError:
                    with self._lock:
                        self.stats.bytes_done -= slot[2]
                    slot[2] = 0
                    # un corte suelto: se repite aquí; sin conexión, el motor espera a que vuelva
                    if retry == 3 or self._cancel.is_set() or not self._dst_online():
                        raise
                    self._cancel.wait(2 ** retry)
            bad = (it.md5 and md5 != it.md5) or (not it.export and got != it.size)
            if not bad:
                break
            engine._remove_quiet(lp(tmp))
            with self._lock:
                self.stats.bytes_done -= slot[2]
            slot[2] = 0
            if attempt == 2:
                raise OSError(0, _("lo bajado de Google Drive no coincide con el original (MD5)"))
            with self._lock:
                self.stats.verify_retries += 1
        try:
            os.utime(lp(tmp), ns=(it.mtime, it.mtime))  # la fecha de Drive: así se reconoce la próxima vez
        except OSError:
            pass
        if kind == "c":
            try:
                self._archive(dst)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._error(dst, exc, _("no se pudo guardar la versión anterior; se sobrescribe"))
                engine._make_writable(lp(dst))
        try:
            os.replace(lp(tmp), lp(dst))
        except PermissionError:
            engine._make_writable(lp(dst))
            try:
                os.replace(lp(tmp), lp(dst))
            except OSError:
                engine._remove_quiet(lp(tmp))
                raise
        except OSError:
            engine._remove_quiet(lp(tmp))
            raise
        if it.md5:
            with self._lock:
                self.stats.verified_copies += 1
        return digest

    # ── integridad: lo dañado en el disco se vuelve a bajar ──
    def _scrub_one(self, rel_dir, name, size, mtime, stored, rb, callback, buf, slot, tid) -> bytes | None:
        rel = os.path.join(rel_dir, name) if rel_dir else name
        it = self._items.get(norm_rel(rel))
        if it is None or not self._same_entry(rel_dir, name, it.size, it.mtime, (name, size, mtime)):
            return None  # ya no está en Drive (o cambió): lo recogerá la próxima sincronización
        src, dst = os.path.join(self.src, rel), os.path.join(self.dst, rel)
        self.active[tid] = [dst, size, 0]
        try:
            try:
                on_disk = engine.file_digest(dst, rb, uncached=True, stop=self._scrub_stop)
            except FileNotFoundError:
                if not engine.BackupJob._dst_online(self):
                    raise
                digest = self._copy_file(src, dst, "n", callback, buf, slot, rb)
                with self._lock:
                    self.stats.scrub_repaired += 1
                self._error(dst, OSError(0, _("faltaba en el destino (¿borrado a mano?); bajado de nuevo de Google "
                                              "Drive")))
                return digest
            with self._lock:
                self.stats.scrub_files += 1
                self.stats.scrub_bytes += size
            if on_disk == stored:
                return on_disk
            if stored is None:
                # sin huella guardada (p. ej. movido): se compara con el MD5 que da Google, sin bajar nada
                if not it.md5 or _md5(dst) == it.md5:
                    return on_disk
            digest = self._copy_file(src, dst, "c", callback, buf, slot, rb)
            with self._lock:
                self.stats.scrub_repaired += 1
            self._error(dst, OSError(0, _("contenido dañado en el destino; bajado de nuevo de Google Drive (la "
                                          "versión dañada está en versiones)")))
            return digest
        except engine._Cancelled:
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

