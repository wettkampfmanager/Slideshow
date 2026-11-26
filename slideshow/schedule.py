"""Zeitregeln: an welchen Tagen und zu welcher Uhrzeit ein Zeitraum aktiv ist.

Unterstützte Arten (``kind``):

* ``daily``        – jeden Tag
* ``weekly``       – an bestimmten Wochentagen (z. B. immer Montags)
* ``monthly_nth``  – am n-ten Wochentag im Monat (z. B. erster Montag, letzter Freitag)
* ``monthly_day``  – an einem festen Kalendertag (z. B. am 15.; 31. = letzter Tag)
* ``once``         – an einem bestimmten Datum

Das Zeitfenster ist minutengenau und schließt die Endminute ein:
``00:00–23:59`` bedeutet den ganzen Tag. Ist die Endzeit kleiner als die
Startzeit, läuft das Fenster über Mitternacht bis zum Folgetag.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping

KINDS = {
    "daily": "Täglich",
    "weekly": "Wöchentlich (Wochentage)",
    "monthly_nth": "Monatlich (n-ter Wochentag)",
    "monthly_day": "Monatlich (fester Tag)",
    "once": "Einmalig (Datum)",
    "period": "Zeitspanne (von–bis, auch über mehrere Tage)",
}

WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

# Negative Zahlen zählen vom Monatsende: -1 = letzter, -2 = vorletzter
NTHS = {1: "1.", 2: "2.", 3: "3.", 4: "4.", 5: "5.", -2: "vorletzten", -1: "letzten"}


def parse_ints(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(sorted({int(v) for v in str(value).split(",") if v.strip()}))


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def parse_time(value: Any) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value)).replace(second=0, microsecond=0)


@dataclass(frozen=True)
class Rule:
    kind: str
    start_time: time
    end_time: time
    weekdays: tuple[int, ...] = ()
    nths: tuple[int, ...] = ()
    monthday: int | None = None
    on_date: date | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    end_date: date | None = None  # nur bei "period": on_date+start_time bis end_date+end_time

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Rule":
        kind = row["kind"]
        if kind not in KINDS:
            raise ValueError(f"Unbekannte Art: {kind}")
        rule = cls(
            kind=kind,
            start_time=parse_time(row["start_time"]),
            end_time=parse_time(row["end_time"]),
            weekdays=parse_ints(row["weekdays"]),
            nths=parse_ints(row["nths"]),
            monthday=int(row["monthday"]) if row["monthday"] not in (None, "") else None,
            on_date=parse_date(row["on_date"]),
            valid_from=parse_date(row["valid_from"]),
            valid_until=parse_date(row["valid_until"]),
            end_date=parse_date(row["end_date"] if "end_date" in row.keys() else None),
        )
        rule.validate()
        return rule

    def validate(self) -> None:
        if any(not 0 <= d <= 6 for d in self.weekdays):
            raise ValueError("Ungültiger Wochentag")
        if any(n not in NTHS for n in self.nths):
            raise ValueError("Ungültige Angabe für n-ten Wochentag")
        if self.kind == "weekly" and not self.weekdays:
            raise ValueError("Bitte mindestens einen Wochentag wählen.")
        if self.kind == "monthly_nth" and (not self.weekdays or not self.nths):
            raise ValueError("Bitte Wochentag und „der wievielte im Monat“ wählen.")
        if self.kind == "monthly_day" and not (self.monthday and 1 <= self.monthday <= 31):
            raise ValueError("Bitte einen Tag zwischen 1 und 31 angeben.")
        if self.kind == "once" and not self.on_date:
            raise ValueError("Bitte ein Datum angeben.")
        if self.kind == "period":
            if not self.on_date or not self.end_date:
                raise ValueError("Bitte Beginn und Ende der Zeitspanne angeben.")
            if self.period_end < self.period_start:
                raise ValueError("Das Ende der Zeitspanne liegt vor dem Beginn.")
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("„Gültig ab“ liegt nach „gültig bis“.")

    @property
    def overnight(self) -> bool:
        return self.kind != "period" and self.end_time < self.start_time

    @property
    def period_start(self) -> datetime:
        assert self.on_date is not None
        return datetime.combine(self.on_date, self.start_time)

    @property
    def period_end(self) -> datetime:
        """Letzte aktive Minute (inklusive)."""
        assert self.end_date is not None
        return datetime.combine(self.end_date, self.end_time)


def _days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def day_matches(rule: Rule, d: date) -> bool:
    """Beginnt an diesem Tag ein Zeitfenster der Regel?"""
    if rule.valid_from and d < rule.valid_from:
        return False
    if rule.valid_until and d > rule.valid_until:
        return False

    if rule.kind == "daily":
        return True
    if rule.kind == "weekly":
        return d.weekday() in rule.weekdays
    if rule.kind == "monthly_nth":
        if d.weekday() not in rule.weekdays:
            return False
        nth = (d.day - 1) // 7 + 1                              # 1 = erster, 2 = zweiter …
        nth_from_end = -((_days_in_month(d) - d.day) // 7 + 1)  # -1 = letzter, -2 = vorletzter
        return nth in rule.nths or nth_from_end in rule.nths
    if rule.kind == "monthly_day":
        assert rule.monthday is not None
        return d.day == min(rule.monthday, _days_in_month(d))
    if rule.kind == "once":
        return d == rule.on_date
    if rule.kind == "period":
        return rule.period_start.date() <= d <= rule.period_end.date()
    return False


def is_active(rule: Rule, now: datetime) -> bool:
    if rule.kind == "period":
        return rule.period_start <= now.replace(second=0, microsecond=0) <= rule.period_end
    t = now.time().replace(second=0, microsecond=0)
    start, end = rule.start_time, rule.end_time
    today = now.date()
    if not rule.overnight:
        return start <= t <= end and day_matches(rule, today)
    if t >= start and day_matches(rule, today):
        return True
    return t <= end and day_matches(rule, today - timedelta(days=1))


def active_segments(rule: Rule, day: date) -> list[tuple[int, int]]:
    """Aktive Abschnitte eines Tages in Minuten seit 0:00, jeweils [von, bis] inklusive."""
    segments: list[tuple[int, int]] = []
    base = datetime.combine(day, time(0, 0))
    start = None
    for minute in range(1440):
        on = is_active(rule, base + timedelta(minutes=minute))
        if on and start is None:
            start = minute
        elif not on and start is not None:
            segments.append((start, minute - 1))
            start = None
    if start is not None:
        segments.append((start, 1439))
    return segments


def _switch_points(rule: Rule, day: date) -> set[datetime]:
    """Zeitpunkte am Kalendertag `day`, an denen die Regel an- oder ausgeht (Kandidaten)."""
    one = timedelta(minutes=1)
    if rule.kind == "period":
        points = {rule.period_start, rule.period_end + one}
    else:
        points = set()
        for start_day in (day - timedelta(days=1), day):  # Vortag wegen Fenstern über Mitternacht
            if not day_matches(rule, start_day):
                continue
            end_day = start_day + timedelta(days=1) if rule.overnight else start_day
            points.add(datetime.combine(start_day, rule.start_time))
            points.add(datetime.combine(end_day, rule.end_time) + one)
    return {p for p in points if p.date() == day}


def find_change(rules: list[Rule], at: datetime, direction: int = 1, horizon_days: int = 400) -> datetime | None:
    """Nächster (direction=1) bzw. vorheriger (-1) Zeitpunkt, an dem sich die Menge der
    aktiven Regeln ändert. Ein Wechsel um c heißt: Zustand um c ≠ Zustand um c - 1 min."""
    at = at.replace(second=0, microsecond=0)
    one = timedelta(minutes=1)

    def state(t: datetime) -> tuple[bool, ...]:
        return tuple(is_active(r, t) for r in rules)

    for offset in range(horizon_days + 1):
        day = at.date() + timedelta(days=offset * direction)
        points = set().union(*(_switch_points(r, day) for r in rules)) if rules else set()
        for p in sorted(points, reverse=direction < 0):
            if (direction > 0 and p <= at) or (direction < 0 and p >= at):
                continue
            if state(p) != state(p - one):
                return p
    return None


def upcoming(rule: Rule, now: datetime, count: int = 3, horizon_days: int = 400) -> list[datetime]:
    """Startzeitpunkte der nächsten Zeitfenster (inkl. eines gerade laufenden)."""
    if rule.kind == "period":
        return [rule.period_start] if rule.period_end + timedelta(seconds=59) >= now else []
    result: list[datetime] = []
    today = now.date()
    for offset in range(-1, horizon_days):
        d = today + timedelta(days=offset)
        if not day_matches(rule, d):
            continue
        start = datetime.combine(d, rule.start_time)
        end_day = d + timedelta(days=1) if rule.overnight else d
        end = datetime.combine(end_day, rule.end_time) + timedelta(seconds=59)
        if end >= now:
            result.append(start)
            if len(result) >= count:
                break
    return result


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " und " + items[-1]


def _days_text(rule: Rule) -> str:
    wd = list(rule.weekdays)
    if rule.kind == "daily" or (rule.kind == "weekly" and wd == list(range(7))):
        return "Täglich"
    if rule.kind == "weekly":
        if wd == [0, 1, 2, 3, 4]:
            return "Werktags (Mo–Fr)"
        if wd == [5, 6]:
            return "Am Wochenende"
        return "Jeden " + _join([WEEKDAYS_LONG[d] for d in wd])
    if rule.kind == "monthly_nth":
        nths = sorted(rule.nths, key=lambda n: n if n > 0 else 100 + n)  # vorletzten vor letzten
        days = " / ".join(WEEKDAYS_LONG[d] for d in wd)
        return f"Jeden {_join([NTHS[n] for n in nths])} {days} im Monat"
    if rule.kind == "monthly_day":
        suffix = " (bzw. Monatsletzten)" if rule.monthday and rule.monthday > 28 else ""
        return f"Jeden {rule.monthday}. des Monats{suffix}"
    if rule.kind == "once" and rule.on_date:
        return f"Am {rule.on_date:%d.%m.%Y}"
    return "?"


def describe(rule: Rule) -> str:
    if rule.kind == "period":
        return f"Vom {rule.period_start:%d.%m.%Y %H:%M} bis {rule.period_end:%d.%m.%Y %H:%M}"
    if rule.start_time == time(0, 0) and rule.end_time == time(23, 59):
        when = "ganztägig"
    else:
        when = f"{rule.start_time:%H:%M}–{rule.end_time:%H:%M}"
        if rule.overnight:
            when += " (bis Folgetag)"
    text = f"{_days_text(rule)}, {when}"
    if rule.valid_from and rule.valid_until:
        text += f" · gültig {rule.valid_from:%d.%m.%Y}–{rule.valid_until:%d.%m.%Y}"
    elif rule.valid_from:
        text += f" · gültig ab {rule.valid_from:%d.%m.%Y}"
    elif rule.valid_until:
        text += f" · gültig bis {rule.valid_until:%d.%m.%Y}"
    return text
