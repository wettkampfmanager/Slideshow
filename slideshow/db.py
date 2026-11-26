"""SQLite-Speicher für Zeiträume, Slides und Einstellungen."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 10,
    kind        TEXT    NOT NULL,
    weekdays    TEXT    NOT NULL DEFAULT '',
    nths        TEXT    NOT NULL DEFAULT '',
    monthday    INTEGER,
    on_date     TEXT,
    start_time  TEXT    NOT NULL,
    end_time    TEXT    NOT NULL,
    valid_from  TEXT,
    valid_until TEXT,
    exclusive   INTEGER NOT NULL DEFAULT 0,  -- Schließzeit: blendet alle anderen aus
    end_date    TEXT                         -- Ende bei kind = 'period'
);

CREATE TABLE IF NOT EXISTS slides (
    id          INTEGER PRIMARY KEY,
    schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    position    INTEGER NOT NULL,
    duration    INTEGER
);
CREATE INDEX IF NOT EXISTS slides_schedule ON slides(schedule_id, position);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "default_duration": "30",   # Sekunden
    "transition_ms": "1000",    # Überblendung, 0 = hart schneiden
    "fit": "contain",           # contain = ganzes Bild, cover = bildschirmfüllend
    "display_width": "1920",
    "display_height": "1080",
    "default_image": "",        # wird gezeigt, wenn kein Zeitraum aktiv ist
    "password_hash": "",        # leer = Standardpasswort
}

SCHEDULE_FIELDS = (
    "name", "enabled", "priority", "kind", "weekdays", "nths", "monthday",
    "on_date", "start_time", "end_time", "valid_from", "valid_until",
    "exclusive", "end_date",
)

# Spalten, die nach der ersten Version dazugekommen sind (für bestehende Datenbanken)
MIGRATIONS = {
    "exclusive": "ALTER TABLE schedules ADD COLUMN exclusive INTEGER NOT NULL DEFAULT 0",
    "end_date": "ALTER TABLE schedules ADD COLUMN end_date TEXT",
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(schedules)")}
            for column, sql in MIGRATIONS.items():
                if column not in existing:
                    conn.execute(sql)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- Einstellungen -------------------------------------------------
    def settings(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        result.update({r["key"]: r["value"] for r in rows})
        return result

    def set_settings(self, **values: str) -> None:
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, str(v)) for k, v in values.items()],
            )

    # --- Zeiträume -----------------------------------------------------
    def schedules(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM schedules ORDER BY priority, name COLLATE NOCASE, id"
            ).fetchall()

    def schedule(self, schedule_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()

    def create_schedule(self, values: dict) -> int:
        cols = ", ".join(SCHEDULE_FIELDS)
        marks = ", ".join("?" for _ in SCHEDULE_FIELDS)
        with self.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO schedules({cols}) VALUES({marks})",
                [values[f] for f in SCHEDULE_FIELDS],
            )
            return int(cur.lastrowid)

    def update_schedule(self, schedule_id: int, values: dict) -> None:
        sets = ", ".join(f"{f} = ?" for f in SCHEDULE_FIELDS)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE schedules SET {sets} WHERE id = ?",
                [values[f] for f in SCHEDULE_FIELDS] + [schedule_id],
            )

    def delete_schedule(self, schedule_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))

    def set_enabled(self, schedule_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE schedules SET enabled = ? WHERE id = ?", (int(enabled), schedule_id))

    def duplicate_schedule(self, schedule_id: int) -> int | None:
        src = self.schedule(schedule_id)
        if src is None:
            return None
        values = {f: src[f] for f in SCHEDULE_FIELDS}
        values["name"] = f"{src['name']} (Kopie)"
        values["enabled"] = 0
        new_id = self.create_schedule(values)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO slides(schedule_id, filename, position, duration) "
                "SELECT ?, filename, position, duration FROM slides WHERE schedule_id = ?",
                (new_id, schedule_id),
            )
        return new_id

    # --- Slides --------------------------------------------------------
    def slides(self, schedule_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM slides WHERE schedule_id = ? ORDER BY position, id", (schedule_id,)
            ).fetchall()

    def slide(self, slide_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM slides WHERE id = ?", (slide_id,)).fetchone()

    def add_slides(self, schedule_id: int, filenames: list[str]) -> None:
        with self.connect() as conn:
            pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM slides WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()[0]
            for name in filenames:
                pos += 1
                conn.execute(
                    "INSERT INTO slides(schedule_id, filename, position) VALUES(?, ?, ?)",
                    (schedule_id, name, pos),
                )

    def reorder_slides(self, schedule_id: int, slide_ids: list[int]) -> None:
        with self.connect() as conn:
            for pos, sid in enumerate(slide_ids):
                conn.execute(
                    "UPDATE slides SET position = ? WHERE id = ? AND schedule_id = ?",
                    (pos, sid, schedule_id),
                )

    def set_slide_duration(self, slide_id: int, duration: int | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE slides SET duration = ? WHERE id = ?", (duration, slide_id))

    def delete_slide(self, slide_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM slides WHERE id = ?", (slide_id,))

    def image_users(self) -> dict[str, list[dict]]:
        """Dateiname -> Zeiträume (id, name), die das Bild verwenden."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT sl.filename, s.id, s.name FROM slides sl "
                "JOIN schedules s ON s.id = sl.schedule_id ORDER BY s.priority, s.name"
            ).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            result.setdefault(r["filename"], []).append({"id": r["id"], "name": r["name"]})
        return result

    def rename_image(self, old: str, new: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE slides SET filename = ? WHERE filename = ?", (new, old))
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'default_image' AND value = ?", (new, old)
            )

    def remove_image_everywhere(self, filename: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM slides WHERE filename = ?", (filename,))


def load_secret(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()
