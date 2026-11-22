"""Copias programadas: a qué horas toca cada una y qué hacer si no se pudo a su hora.

Cada programación es una copia (un origen y un disco o carpeta de destino) con un horario:
ciertos días de la semana o un día del mes, a una hora o varias veces al día. La app mira cada
poco qué programaciones tienen una hora vencida sin atender; si el equipo estaba apagado o el
disco desconectado, la copia se hace en cuanto se pueda (una sola vez aunque se hayan pasado
varias horas) o, si así se eligió, se da por perdida. Este módulo es solo la lógica (sin Qt) para
poder probarla aparte: la hora del sistema se pasa siempre como argumento.
"""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta

WEEK = list(range(7))            # 0 = lunes … 6 = domingo, como date.weekday()
WORKDAYS = [0, 1, 2, 3, 4]
WEEKEND = [5, 6]
EVERY_OPTIONS = (0, 1, 2, 3, 4, 6, 8, 12)  # horas entre copias (0 = una vez al día)
GRACE = timedelta(minutes=15)    # sin «hacerla en cuanto se pueda»: margen para llegar a tiempo
HORIZON = 400                    # días que se miran hacia delante o hacia atrás
RESULTS = ("ok", "warn", "failed", "cancelled", "held", "missed")


def parse_time(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def parse_hhmm(text: str, default: time = time(21, 0)) -> time:
    try:
        h, m = (int(x) for x in str(text).split(":", 1))
        return time(h, m)
    except (TypeError, ValueError):
        return default


def hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


@dataclass
class Plan:
    id: str                       # el de la copia habitual (reminders.pair_id)
    src: str
    dst: str
    disk_id: str = ""
    dst_rel: str = ""
    label: str = ""
    enabled: bool = True
    kind: str = "weekly"          # weekly (días de la semana) | monthly (un día del mes)
    days: list = field(default_factory=lambda: list(WEEK))
    at: str = "21:00"
    every: int = 0                # horas entre copias dentro del día; 0 = solo a la hora «at»
    until: str = "23:00"          # con «every»: la última copia del día, como muy tarde
    month_day: int = 1            # 1…31 (en meses más cortos, su último día); 0 = el último día
    catch_up: bool = True         # equipo apagado o disco desconectado: hacerla en cuanto se pueda
    created: str = ""
    last_slot: str = ""           # la última hora programada ya atendida (hecha, perdida o saltada)
    waiting: str = ""             # hora vencida que espera al disco, al origen o al cargador
    waiting_for: str = ""         # disk | src | power
    last_run: str = ""
    last_end: str = ""
    last_result: str = ""         # uno de RESULTS
    last_detail: str = ""
    last_log: str = ""
    runs: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan | None":
        if not isinstance(d, dict) or not d.get("id") or not d.get("src"):
            return None
        known = {f.name for f in fields(cls)}
        plan = cls(**{k: v for k, v in d.items() if k in known})
        plan.kind = plan.kind if plan.kind in ("weekly", "monthly") else "weekly"
        plan.days = sorted({int(x) for x in plan.days if str(x).isdigit() and 0 <= int(x) <= 6})
        plan.every = int(plan.every) if int(plan.every or 0) in EVERY_OPTIONS else 0
        plan.month_day = min(31, max(0, int(plan.month_day or 0)))
        return plan

    # ── el horario ──
    def times(self) -> list[time]:
        """Las horas de cada día que toca, en orden."""
        start = parse_hhmm(self.at)
        if self.kind != "weekly" or not self.every:
            return [start]
        end = parse_hhmm(self.until, time(23, 59))
        out, minutes = [], start.hour * 60 + start.minute
        limit = end.hour * 60 + end.minute
        while minutes <= limit and minutes < 24 * 60:
            out.append(time(minutes // 60, minutes % 60))
            minutes += self.every * 60
        return out or [start]

    def on_day(self, d: date) -> bool:
        if self.kind == "monthly":
            last = calendar.monthrange(d.year, d.month)[1]
            want = last if self.month_day in (0,) else min(self.month_day, last)
            return d.day == want
        return d.weekday() in self.days

    def next_slot(self, after: datetime) -> datetime | None:
        """La primera hora programada estrictamente posterior a `after`."""
        times = self.times()
        for offset in range(HORIZON):
            d = after.date() + timedelta(days=offset)
            if not self.on_day(d):
                continue
            for t in times:
                when = datetime.combine(d, t)
                if when > after:
                    return when
        return None

    def latest_slot(self, now: datetime, since: datetime | None = None) -> datetime | None:
        """La última hora programada que ya ha llegado (≤ now) y es posterior a `since`."""
        times = list(reversed(self.times()))
        for offset in range(HORIZON):
            d = now.date() - timedelta(days=offset)
            if since is not None and d < since.date():
                return None
            if not self.on_day(d):
                continue
            for t in times:
                when = datetime.combine(d, t)
                if when <= now and (since is None or when > since):
                    return when
        return None

    def upcoming(self, now: datetime, n: int = 3) -> list[datetime]:
        out, after = [], now
        for _i in range(n):
            nxt = self.next_slot(after)
            if nxt is None:
                break
            out.append(nxt)
            after = nxt
        return out

    def base(self, now: datetime) -> datetime:
        """Desde cuándo se cuentan las horas pendientes."""
        return parse_time(self.last_slot) or parse_time(self.created) or now

    def state(self, now: datetime) -> tuple[str, datetime | None]:
        """(«due», hora) si toca hacerla ya; («missed», hora) si se pasó la hora y no se recupera;
        («idle», próxima hora) si no toca todavía; («off», None) si está en pausa o no tiene días."""
        if not self.enabled or (self.kind == "weekly" and not self.days):
            return "off", None
        base = self.base(now)
        latest = self.latest_slot(now, since=base)
        if latest is None:
            return "idle", self.next_slot(max(now, base))
        if self.catch_up or now - latest <= GRACE:
            return "due", latest
        return "missed", latest

    def missed_between(self, now: datetime) -> int:
        """Cuántas horas programadas se pasaron sin hacer desde la última atendida (para el aviso)."""
        count, after = 0, self.base(now)
        while count < 1000:
            nxt = self.next_slot(after)
            if nxt is None or nxt > now:
                break
            count += 1
            after = nxt
        return count

    def same_schedule(self, other: "Plan") -> bool:
        keys = ("enabled", "kind", "days", "at", "every", "until", "month_day", "catch_up")
        return all(getattr(self, k) == getattr(other, k) for k in keys)


class Plans:
    """Las programaciones, guardadas como lista JSON en los ajustes."""

    def __init__(self, items: list | None = None):
        self.items: dict[str, Plan] = {}
        for d in items or []:
            try:
                plan = Plan.from_dict(d)
            except (TypeError, ValueError):
                plan = None
            if plan is not None:
                self.items[plan.id] = plan

    def to_list(self) -> list[dict]:
        return [p.to_dict() for p in self.ordered()]

    def ordered(self, now: datetime | None = None) -> list[Plan]:
        now = now or datetime.now()

        def key(p: Plan):
            st, when = p.state(now)
            return (st == "off", when or datetime.max, p.src.lower())
        return sorted(self.items.values(), key=key)

    def get(self, pid: str) -> Plan | None:
        return self.items.get(pid)

    def put(self, plan: Plan, now: datetime) -> Plan:
        """Guarda una programación nueva o cambiada. Si cambia el horario, se empieza a contar
        desde ahora: lo que «tocaba» con el horario anterior no se recupera."""
        old = self.items.get(plan.id)
        if old is None:
            plan.created = plan.created or stamp(now)
            plan.last_slot = stamp(now)
        else:
            for k in ("created", "last_run", "last_end", "last_result", "last_detail", "last_log", "runs"):
                setattr(plan, k, getattr(old, k))
            if old.same_schedule(plan):
                plan.last_slot, plan.waiting, plan.waiting_for = old.last_slot, old.waiting, old.waiting_for
            else:
                plan.last_slot, plan.waiting, plan.waiting_for = stamp(now), "", ""
        self.items[plan.id] = plan
        return plan

    def remove(self, pid: str) -> None:
        self.items.pop(pid, None)

    def next_run(self, now: datetime) -> tuple[Plan, datetime] | None:
        """La próxima copia que va a hacerse (las vencidas que esperan, ahora)."""
        best = None
        for p in self.items.values():
            st, when = p.state(now)
            if st == "due":
                when = now
            if st in ("off", "missed") or when is None:
                continue
            if best is None or when < best[1]:
                best = (p, when)
        return best

    def wake_at(self, now: datetime) -> datetime | None:
        """La próxima hora programada (para despertar el equipo un poco antes)."""
        times = [p.next_slot(max(now, p.base(now))) for p in self.items.values()
                 if p.state(now)[0] != "off"]
        times = [t for t in times if t is not None]
        return min(times) if times else None

    # ── lo que pasa al atenderla ──
    def start(self, pid: str, slot: datetime, now: datetime) -> None:
        p = self.items.get(pid)
        if p:
            p.last_slot = stamp(max(slot, parse_time(p.last_slot) or slot))
            p.waiting = p.waiting_for = ""
            p.last_run = stamp(now)

    def finish(self, pid: str, result: str, now: datetime, detail: str = "", log: str = "") -> None:
        p = self.items.get(pid)
        if p:
            p.last_result = result
            p.last_detail = detail
            p.last_end = stamp(now)
            p.last_log = log
            if result in ("ok", "warn"):
                p.runs += 1

    def wait(self, pid: str, slot: datetime, why: str) -> bool:
        """La hora llegó pero falta el disco, el origen o el cargador. True si es un aviso nuevo."""
        p = self.items.get(pid)
        if p is None:
            return False
        fresh = p.waiting != stamp(slot) or p.waiting_for != why
        p.waiting, p.waiting_for = stamp(slot), why
        return fresh

    def miss(self, pid: str, slot: datetime, now: datetime) -> None:
        p = self.items.get(pid)
        if p:
            p.last_slot = stamp(slot)
            p.waiting = p.waiting_for = ""
            p.last_result = "missed"
            p.last_detail = ""
            p.last_end = stamp(now)

    def skip_next(self, pid: str, now: datetime) -> datetime | None:
        """Salta la próxima copia (o la que espera). Devuelve la hora saltada."""
        p = self.items.get(pid)
        if p is None:
            return None
        st, when = p.state(now)
        if st == "due":
            target = when
        else:
            target = p.next_slot(max(now, p.base(now)))
        if target is None:
            return None
        p.last_slot = stamp(target)
        p.waiting = p.waiting_for = ""
        return target
