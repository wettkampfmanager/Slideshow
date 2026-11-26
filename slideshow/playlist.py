"""Ermittelt, welche Bilder zu einem Zeitpunkt in welcher Reihenfolge laufen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import schedule as sched
from .db import Database
from .images import ImageStore


@dataclass
class Slide:
    filename: str
    duration: int
    custom_duration: bool


@dataclass
class Block:
    schedule_id: int | None
    name: str
    priority: int
    exclusive: bool = False
    slides: list[Slide] = field(default_factory=list)


@dataclass
class Playlist:
    at: datetime
    blocks: list[Block]
    fallback: bool  # True = kein Zeitraum aktiv, Standardbild wird gezeigt
    closed: bool = False  # True = Schließzeit aktiv, alle anderen Zeiträume ignoriert

    @property
    def slides(self) -> list[Slide]:
        return [s for b in self.blocks for s in b.slides]

    @property
    def total_seconds(self) -> int:
        return sum(s.duration for s in self.slides)


def build_playlist(db: Database, store: ImageStore, at: datetime) -> Playlist:
    """Alle aktiven Zeiträume, blockweise nach Priorität (kleine Zahl zuerst).

    Ist eine Schließzeit aktiv, zählen nur Schließzeiten. Haben diese keine
    Bilder, bleibt der Bildschirm schwarz (kein Standardbild).

    Innerhalb eines Blocks gilt die im Zeitraum festgelegte Reihenfolge. Bilder,
    die nicht mehr im Pool liegen, werden übersprungen. Läuft nichts, wird das
    Standardbild gezeigt (falls eines gesetzt ist).
    """
    settings = db.settings()
    default_duration = int(settings["default_duration"])
    pool = set(store.list())

    blocks: list[Block] = []
    for row in db.schedules():
        if not row["enabled"]:
            continue
        try:
            rule = sched.Rule.from_row(row)
        except ValueError:
            continue
        if not sched.is_active(rule, at):
            continue
        block = Block(row["id"], row["name"], row["priority"], bool(row["exclusive"]))
        for s in db.slides(row["id"]):
            if s["filename"] not in pool:
                continue
            custom = s["duration"] is not None
            block.slides.append(Slide(s["filename"], s["duration"] if custom else default_duration, custom))
        blocks.append(block)

    closures = [b for b in blocks if b.exclusive]
    if closures:
        return Playlist(at, closures, fallback=False, closed=True)

    if any(b.slides for b in blocks):
        return Playlist(at, blocks, fallback=False)

    default_image = settings["default_image"]
    if default_image and default_image in pool:
        fb = Block(None, "Standardbild", 0, slides=[Slide(default_image, default_duration, False)])
        return Playlist(at, [fb], fallback=True)
    return Playlist(at, [], fallback=True)
