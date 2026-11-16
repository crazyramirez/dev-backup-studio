"""Copias habituales y recordatorios.

Cada sincronización completa registra una «copia habitual»: qué origen se copió, a qué disco o
carpeta y cuándo empezó (lo que cambie después ya no está en la copia). Cada cierto tiempo la app
mira, sin abrir archivos, si el origen ha cambiado desde entonces y, si han pasado los días elegidos,
lo recuerda una vez por periodo. Este módulo es solo la lógica (sin Qt) para poder probarla aparte.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta

RECHECK = timedelta(hours=6)  # sin cambios u origen desconectado: volver a mirar al cabo de 6 h
MAX_COPIES = 24
OK_RESULTS = {"Al día", "Completado"}  # «Resultado» de los registros de copias terminadas


def pair_id(src: str, disk_id: str, dst_rel: str, dst: str) -> str:
    """Identidad de una copia: el origen y el disco (aunque cambie de letra) o, sin marca, la carpeta."""
    where = f"{disk_id}|{os.path.normcase(dst_rel)}" if disk_id else os.path.normcase(os.path.normpath(dst))
    key = f"{os.path.normcase(os.path.normpath(src))}|{where}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _under(path: str, root: str) -> bool:
    path, root = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def parse_time(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def short_name(path: str) -> str:
    """«D:\\DEV» → «DEV»; una unidad entera se queda en «F:»."""
    path = os.path.normpath(path)
    name = os.path.basename(path.rstrip("\\/"))
    return name or path.rstrip("\\/") or path


def has_changes(result: dict | None) -> bool:
    return bool(result) and not result.get("missing") and bool(result.get("files") or result.get("dirs"))


def changes_text(result: dict, fmt_int=str, fmt_bytes=lambda n: f"{n} B") -> str:
    """«37 archivos nuevos o modificados (1,2 GB)» o, si solo cambiaron carpetas, borrados y movidos."""
    files, dirs = result.get("files", 0), result.get("dirs", 0)
    more = "" if result.get("complete", True) else "más de "
    if files:
        noun = "archivo nuevo o modificado" if files == 1 and not more else "archivos nuevos o modificados"
        return f"{more}{fmt_int(files)} {noun} ({fmt_bytes(result.get('bytes', 0))})"
    if dirs:
        return f"archivos borrados, movidos o renombrados en {fmt_int(dirs)} carpeta{'s' if dirs != 1 else ''}"
    return "sin cambios"


class Registry:
    """Las copias habituales, guardadas como lista JSON en los ajustes."""

    def __init__(self, items: list | None = None):
        self.items: dict[str, dict] = {}
        for it in items or []:
            if isinstance(it, dict) and it.get("id") and it.get("src") and parse_time(it.get("last_ok")):
                self.items[it["id"]] = it

    def to_list(self) -> list[dict]:
        return sorted(self.items.values(), key=lambda it: it.get("last_ok", ""), reverse=True)

    def get(self, pid: str) -> dict | None:
        return self.items.get(pid)

    def remove(self, pid: str) -> None:
        self.items.pop(pid, None)

    def record(self, src: str, dst: str, disk_id: str, dst_rel: str, label: str, started: datetime,
               patterns, excluded, copied: bool = True, now: datetime | None = None,
               only_existing: bool = False) -> dict | None:
        """Apunta una copia completa (o un análisis que encontró el destino al día)."""
        now = now or datetime.now()
        pid = pair_id(src, disk_id, dst_rel, dst)
        it = self.items.get(pid)
        if it is None:
            if only_existing:
                return None
            it = {"id": pid, "first": stamp(started), "copies": 0, "remind": True}
            self.items[pid] = it
        previous = parse_time(it.get("last_ok"))
        if previous is None or started >= previous:
            it["last_ok"] = stamp(started)
        it.update(src=os.path.normpath(src), dst=os.path.normpath(dst), disk_id=disk_id, dst_rel=dst_rel,
                  label=label or it.get("label", ""), patterns=sorted(patterns), excluded=sorted(excluded),
                  alert=False, snooze_until="", next_check="",
                  pending={"files": 0, "dirs": 0, "bytes": 0, "checked": stamp(now)})
        if copied:
            it["copies"] = it.get("copies", 0) + 1
            it["last_end"] = stamp(now)
        if disk_id:
            # la misma copia apuntada antes solo por su ruta (sin marca de disco): pasa a ser esta
            for other in list(self.items.values()):
                if (other is not it and not other.get("disk_id") and _same(other["src"], src)
                        and _same(other.get("dst", ""), dst)):
                    self._merge(it, other)
        self._trim()
        return it

    def _merge(self, keep: dict, other: dict) -> None:
        """Une dos apuntes de la misma copia: suma copias y conserva la más reciente."""
        self.items.pop(other["id"], None)
        keep["copies"] = keep.get("copies", 0) + other.get("copies", 0)
        keep["first"] = min(keep.get("first") or other.get("first", ""), other.get("first") or keep.get("first", ""))
        keep["remind"] = keep.get("remind", True) and other.get("remind", True)
        if (other.get("last_ok") or "") > (keep.get("last_ok") or ""):
            for key in ("last_ok", "last_end", "pending", "alert", "snooze_until", "next_check", "patterns",
                        "excluded"):
                if key in other:
                    keep[key] = other[key]

    def adopt_disk(self, disk_id: str, root: str, rel_of, label: str = "", must_exist: bool = False) -> int:
        """Las copias apuntadas solo por ruta en la unidad `root` pasan a llevar la marca de ese disco
        (así se reconocen aunque cambie de letra). Con `must_exist`, solo si su carpeta está en él.
        Devuelve cuántas cambiaron."""
        if not disk_id or not root:
            return 0
        changed = 0
        for it in list(self.items.values()):
            dst = it.get("dst", "")
            if it.get("disk_id") or not dst or not _under(dst, root):
                continue
            if must_exist and not os.path.isdir(dst):
                continue
            rel = rel_of(dst)
            self.items.pop(it["id"], None)
            it.update(disk_id=disk_id, dst_rel=rel, label=it.get("label") or label)
            it["id"] = pair_id(it["src"], disk_id, rel, dst)
            twin = self.items.get(it["id"])
            if twin is None:
                self.items[it["id"]] = it
            else:
                self._merge(twin, it)
            changed += 1
        return changed

    def _trim(self) -> None:
        for it in self.to_list()[MAX_COPIES:]:
            self.items.pop(it["id"], None)

    # ── recordatorios ──
    def is_due(self, it: dict, now: datetime, days: int, ignore_next_check: bool = False) -> bool:
        """Toca recordar: hace `days` días o más de la última copia y no está aplazado."""
        if days <= 0 or not it.get("remind", True):
            return False
        last = parse_time(it.get("last_ok"))
        if last is None or now - last < timedelta(days=days):
            return False
        snooze = parse_time(it.get("snooze_until"))
        if snooze and now < snooze:
            return False
        nxt = parse_time(it.get("next_check"))
        return ignore_next_check or nxt is None or now >= nxt

    def due(self, now: datetime, days: int) -> list[dict]:
        return [it for it in self.to_list() if self.is_due(it, now, days)]

    def apply_check(self, pid: str, result: dict, now: datetime, days: int, enabled: bool = True) -> str:
        """Guarda lo que se encontró en el origen. Devuelve «new» si hay que avisar ahora, «alert» si
        ya estaba avisado y sigue pendiente, «clear» si ya no hay nada pendiente o «» si no cambia nada."""
        it = self.items.get(pid)
        if it is None or not result.get("complete", True):
            return ""
        it["pending"] = {"files": result.get("files", 0), "dirs": result.get("dirs", 0),
                         "bytes": result.get("bytes", 0), "latest": result.get("latest", ""),
                         "missing": bool(result.get("missing")), "checked": stamp(now)}
        if not has_changes(result):
            it["next_check"] = stamp(now + RECHECK)
            if it.get("alert") and not result.get("missing"):
                it["alert"] = False
                return "clear"
            return ""
        if not enabled or not self.is_due(it, now, days, ignore_next_check=True):
            if it.get("alert") and not enabled:
                it["alert"] = False
                return "clear"
            return "alert" if it.get("alert") else ""
        was = bool(it.get("alert"))
        it["alert"] = True
        it["last_notified"] = stamp(now)
        it["snooze_until"] = stamp(now + timedelta(days=days))  # el siguiente aviso, dentro de otro periodo
        return "alert" if was else "new"

    def alerts(self) -> list[dict]:
        return [it for it in self.to_list() if it.get("alert") and it.get("remind", True)]

    def snooze(self, pid: str, until: datetime) -> None:
        it = self.items.get(pid)
        if it:
            it["alert"] = False
            it["snooze_until"] = stamp(until)

    def dismiss(self, pid: str) -> None:
        """Cerrar el aviso: no vuelve hasta el siguiente periodo (ya fijado al avisar)."""
        it = self.items.get(pid)
        if it:
            it["alert"] = False

    def set_remind(self, pid: str, on: bool) -> None:
        it = self.items.get(pid)
        if it:
            it["remind"] = on
            if not on:
                it["alert"] = False


# ── copias anteriores a los recordatorios: se recuperan de los registros ─────

_LINE = re.compile(r"^(Inicio|Resultado|Origen|Destino|Reglas):\s+(.*)$")


def parse_log(path: str) -> dict | None:
    """Cabecera de un registro de copia («copia_*.log»): inicio, resultado, origen, destino y reglas."""
    found: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(14):
                m = _LINE.match(fh.readline().rstrip("\n"))
                if m:
                    found[m[1]] = m[2].strip()
    except OSError:
        return None
    started = parse_time(found.get("Inicio", "").replace(" ", "T"))
    if not started or not found.get("Origen") or not found.get("Destino"):
        return None
    rules = found.get("Reglas", "")
    return {"inicio": started, "ok": found.get("Resultado") in OK_RESULTS, "origen": found["Origen"],
            "destino": found["Destino"],
            "reglas": [] if rules in ("", "ninguna") else [r.strip() for r in rules.split(",") if r.strip()]}


def seed_from_logs(log_dir: str, resolve) -> Registry:
    """Registro inicial a partir de las copias terminadas que hay en los registros.

    `resolve(destino)` devuelve (id_del_disco, ruta_en_el_disco, etiqueta) o ("", "", "") si no se sabe.
    """
    reg = Registry()
    try:
        names = sorted(n for n in os.listdir(log_dir) if n.startswith("copia_") and n.endswith(".log"))
    except OSError:
        return reg
    for name in names:
        info = parse_log(os.path.join(log_dir, name))
        if not info or not info["ok"]:
            continue
        disk_id, dst_rel, label = resolve(info["destino"])
        it = reg.record(info["origen"], info["destino"], disk_id, dst_rel, label, info["inicio"],
                        info["reglas"], [], now=info["inicio"])
        if it is not None:
            it.pop("pending", None)  # no se ha mirado todavía
    return reg
