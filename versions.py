"""
Versiones: lo que la sincronización ha apartado del destino.

Cada sincronización que borra o sustituye algo lo mueve (sin copiarlo) a

    <disco de copia>/.devbackup/versiones/<AAAA-MM-DD HHMMSS>/<ruta del destino en el disco>/...

Este módulo lo lee por fechas, lo devuelve al origen sin sobrescribir nunca nada y quita lo
antiguo: por plazo (lo hace engine.BackupJob al sincronizar) o porque falta espacio.
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime

from engine import META_DIR, STAMP_FMT, TMP_SUFFIX, _rmtree_force, lp, rel_to_volume
from i18n import _

VERSIONS_DIR = "versiones"
SAME_MTIME_S = 2.0  # FAT y muchos servidores guardan la fecha con 2 s de precisión


def versions_base(meta_root: str) -> str:
    return os.path.join(meta_root, META_DIR, VERSIONS_DIR)


@dataclass
class Snapshot:
    """Lo que apartó una sincronización concreta."""
    stamp: datetime
    name: str   # nombre de la carpeta: «AAAA-MM-DD HHMMSS»
    base: str   # .devbackup/versiones/<name>: de todas las copias que van a ese disco
    path: str   # la parte de este destino
    files: list[tuple[str, int, int]] = field(default_factory=list)  # (ruta relativa, tamaño, fecha_ns)

    @property
    def size(self) -> int:
        return sum(f[1] for f in self.files)


def list_snapshots(meta_root: str, dst: str | None = None) -> list[Snapshot]:
    """Fechas con versiones, de la más reciente a la más antigua.

    Con «dst», solo las que tienen algo de esa carpeta de destino.
    """
    base = versions_base(meta_root)
    try:
        names = os.listdir(lp(base))
    except OSError:
        return []
    sub = rel_to_volume(dst) if dst else ""
    out = []
    for name in names:
        try:
            when = datetime.strptime(name, STAMP_FMT)
        except ValueError:
            continue
        snap_base = os.path.join(base, name)
        path = os.path.join(snap_base, sub) if sub else snap_base
        if os.path.isdir(lp(path)):
            out.append(Snapshot(when, name, snap_base, path))
    out.sort(key=lambda s: s.stamp, reverse=True)
    return out


WALK_THREADS = 16  # en una carpeta de red cada listado espera a la red: se piden varios a la vez


def walk_many(roots: list[str], cancel: threading.Event | None = None,
              threads: int = WALK_THREADS) -> list[list[tuple[str, int, int]]]:
    """Archivos bajo cada carpeta de «roots»: (ruta relativa, tamaño, fecha de modificación en ns).

    Las carpetas se leen en paralelo: por SMB cada listado tarda lo que un viaje a la red, y en
    serie un árbol de mil carpetas son decenas de segundos.
    """
    results: list[list[tuple[str, int, int]]] = [[] for _ in roots]
    if not roots:
        return results
    work: queue.Queue = queue.Queue()
    lock = threading.Lock()
    for i in range(len(roots)):
        work.put((i, ""))

    def worker():
        while True:
            item = work.get()
            if item is None:
                work.task_done()
                return
            i, rel = item
            try:
                if cancel is not None and cancel.is_set():
                    continue
                base = roots[i]
                found = []
                with os.scandir(lp(os.path.join(base, rel) if rel else base)) as it:
                    for e in it:
                        child = os.path.join(rel, e.name) if rel else e.name
                        try:
                            if e.is_dir(follow_symlinks=False):
                                work.put((i, child))
                            elif e.is_file(follow_symlinks=False) and not e.name.endswith(TMP_SUFFIX):
                                st = e.stat(follow_symlinks=False)
                                found.append((child, st.st_size, st.st_mtime_ns))
                        except OSError:
                            continue
                if found:
                    with lock:
                        results[i].extend(found)
            except OSError:
                pass
            finally:
                work.task_done()

    pool = [threading.Thread(target=worker, name="versiones-leer", daemon=True)
            for _ in range(max(1, threads))]
    for t in pool:
        t.start()
    work.join()
    for _t in pool:
        work.put(None)
    for t in pool:
        t.join()
    return results


def walk(root: str, cancel: threading.Event | None = None) -> list[tuple[str, int, int]]:
    """Archivos bajo «root»: (ruta relativa, tamaño, fecha de modificación en ns)."""
    return walk_many([root], cancel)[0]


def load(meta_root: str, dst: str, cancel: threading.Event | None = None,
         cache: dict | None = None) -> list[Snapshot]:
    """Fechas de este destino con sus archivos ya leídos.

    Lo apartado en una fecha ya no cambia: con «cache» ({nombre de la fecha: archivos}) solo se
    leen las fechas nuevas. Al volver, «cache» tiene exactamente las fechas que hay ahora.
    """
    snaps = list_snapshots(meta_root, dst)
    known = cache or {}
    todo = [s for s in snaps if s.name not in known]
    for s, files in zip(todo, walk_many([s.path for s in todo], cancel)):
        s.files = files
    for s in snaps:
        if s.name in known:
            s.files = [tuple(f) for f in known[s.name]]
    if cache is not None:
        cache.clear()
        cache.update({s.name: s.files for s in snaps})
    return [s for s in snaps if s.files]


def recovered_name(path: str, when: datetime) -> str:
    """«informe.pdf» → «informe (recuperado 2026-11-19 1432).pdf», sin pisar nada que exista."""
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    if not stem:  # «.env»: sin extensión
        stem, ext = name, ""
    tag = when.strftime("%Y-%m-%d %H%M")
    for n in range(1, 1000):
        extra = f" {n}" if n > 1 else ""
        candidate = os.path.join(folder, f"{stem} ({_('recuperado')} {tag}{extra}){ext}")
        if not os.path.lexists(lp(candidate)):
            return candidate
    raise OSError(0, _("demasiados archivos recuperados con el mismo nombre"))


def same_file(a: os.stat_result, size: int, mtime_ns: int) -> bool:
    return a.st_size == size and abs(a.st_mtime_ns - mtime_ns) <= SAME_MTIME_S * 1e9


@dataclass
class RestoreResult:
    restored: int = 0      # en su sitio
    renamed: int = 0       # ya había otro archivo ahí: guardado al lado con otro nombre
    same: int = 0          # ya estaba igual: no hacía falta
    bytes: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.restored + self.renamed


def restore(items: list[tuple[str, str, int, int]], when: datetime | None = None,
            cancel: threading.Event | None = None, progress=None) -> RestoreResult:
    """Copia versiones de vuelta. «items»: (archivo en versiones, destino, tamaño, fecha_ns).

    Nunca sobrescribe: si en el destino ya hay un archivo distinto, la versión se guarda al lado
    con «(recuperado …)» en el nombre. La versión se copia, no se mueve: sigue disponible.
    """
    when = when or datetime.now()
    res = RestoreResult()
    for i, (src, target, size, mtime_ns) in enumerate(items):
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
                if os.path.isfile(lp(target)) and same_file(st, size, mtime_ns):
                    res.same += 1
                    res.targets.append(target)
                    continue
                target = recovered_name(target, when)
                renamed = True
            os.makedirs(lp(os.path.dirname(target)), exist_ok=True)
            tmp = target + TMP_SUFFIX
            try:
                shutil.copy2(lp(src), lp(tmp))
                if os.path.getsize(lp(tmp)) != os.path.getsize(lp(src)):
                    raise OSError(0, _("la copia no tiene el tamaño del original"))
                if os.path.lexists(lp(target)):  # apareció mientras se copiaba: tampoco se pisa
                    target = recovered_name(target, when)
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
    if progress:
        progress(len(items), len(items), "")
    return res


def _prune_empty(path: str, stop: str) -> None:
    """Quita las carpetas vacías desde «path» hacia arriba, sin pasar de «stop»."""
    stop = os.path.normcase(os.path.normpath(stop))
    cur = os.path.normpath(path)
    while os.path.normcase(cur) != stop and os.path.normcase(cur).startswith(stop):
        try:
            os.rmdir(lp(cur))
        except OSError:
            return
        cur = os.path.dirname(cur)


def delete_snapshot(snap: Snapshot, meta_root: str) -> None:
    """Elimina lo de este destino en esa fecha (y la fecha entera si se queda vacía)."""
    _rmtree_force(snap.path)
    _prune_empty(os.path.dirname(snap.path), versions_base(meta_root))
    try:
        os.rmdir(lp(snap.base))
    except OSError:
        pass


def free_space(meta_root: str, need: int, keep: set[str] = frozenset(),
               cancel: threading.Event | None = None) -> tuple[int, int]:
    """Quita las fechas más antiguas (de cualquier copia del disco) hasta que haya «need» bytes libres.

    Devuelve (fechas quitadas, bytes liberados). Nunca toca las fechas de «keep».
    """
    def free() -> int:
        try:
            return shutil.disk_usage(meta_root).free
        except OSError:
            return 0

    removed = freed = 0
    start = free()
    if start >= need:
        return 0, 0
    for snap in sorted(list_snapshots(meta_root), key=lambda s: s.stamp):
        if cancel is not None and cancel.is_set():
            break
        if snap.name in keep:
            continue
        try:
            _rmtree_force(snap.base)
        except OSError:
            continue
        removed += 1
        now = free()
        freed = max(0, now - start)
        if now >= need:
            break
    return removed, freed
