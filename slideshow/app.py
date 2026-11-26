"""FastAPI-Anwendung: Admin-Oberfläche (/admin), Player (/player) und API (/api).

Start (Entwicklung):
    uvicorn --factory slideshow.app:create_app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from . import schedule as sched
from .db import Database, load_secret
from .images import DuplicateImage, ImageError, ImageStore
from .playlist import Playlist, build_playlist

BASE_DIR = Path(__file__).resolve().parent

NEW_SCHEDULE = {
    "id": None, "name": "", "enabled": 1, "priority": 10, "kind": "daily",
    "weekdays": "", "nths": "1", "monthday": "", "on_date": "",
    "start_time": "00:00", "end_time": "23:59", "valid_from": "", "valid_until": "",
    "exclusive": 0, "end_date": "",
}


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_schedule_form(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    values = {
        "name": (form.get("name") or "").strip(),
        "enabled": 1 if form.get("enabled") else 0,
        "priority": _int(form.get("priority"), 10),
        "kind": form.get("kind") or "daily",
        "weekdays": ",".join(str(d) for d in sorted({_int(v) for v in form.getlist("weekdays")} - {None})),
        "nths": ",".join(str(n) for n in sorted({_int(v) for v in form.getlist("nths")} - {None})),
        "monthday": _int(form.get("monthday")),
        "on_date": (form.get("on_date") or "").strip() or None,
        "start_time": (form.get("start_time") or "").strip(),
        "end_time": (form.get("end_time") or "").strip(),
        "valid_from": (form.get("valid_from") or "").strip() or None,
        "valid_until": (form.get("valid_until") or "").strip() or None,
        "exclusive": 1 if form.get("exclusive") else 0,
        "end_date": None,
    }
    if values["kind"] == "period":
        # Zeitspanne kommt aus zwei datetime-local-Feldern
        for prefix, date_key, time_key in (("period_start", "on_date", "start_time"), ("period_end", "end_date", "end_time")):
            raw = (form.get(prefix) or "").strip()
            values[date_key], _, values[time_key] = raw.partition("T")
            values[date_key] = values[date_key] or None
        values["valid_from"] = values["valid_until"] = None
    if not values["name"]:
        errors.append("Bitte einen Namen angeben.")
    # Felder, die für die gewählte Art keine Rolle spielen, leeren
    kind = values["kind"]
    if kind not in ("weekly", "monthly_nth"):
        values["weekdays"] = ""
    if kind != "monthly_nth":
        values["nths"] = ""
    if kind != "monthly_day":
        values["monthday"] = None
    if kind not in ("once", "period"):
        values["on_date"] = None
    try:
        rule = sched.Rule.from_row(values)
        values["start_time"] = f"{rule.start_time:%H:%M}"
        values["end_time"] = f"{rule.end_time:%H:%M}"
    except ValueError as exc:
        msg = str(exc)
        if "isoformat" in msg or "Invalid" in msg:
            msg = "Bitte gültige Datums- und Uhrzeitangaben machen."
        errors.append(msg)
    return values, errors


def create_app(data_dir: Path | str | None = None, warmup: bool = True) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("SLIDESHOW_DATA") or BASE_DIR.parent / "data")
    pool_dir = Path(os.environ.get("SLIDESHOW_POOL") or data_dir / "pool")
    data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "slideshow.db")
    store = ImageStore(pool_dir, data_dir / "cache")

    app = FastAPI(title="Slideshow", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.store = store
    app.add_middleware(
        SessionMiddleware,
        secret_key=load_secret(data_dir / "secret.key"),
        same_site="lax",
        max_age=30 * 24 * 3600,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    def display_size() -> tuple[int, int]:
        s = db.settings()
        return int(s["display_width"]), int(s["display_height"])

    def thumb_url(name: str) -> str:
        return f"/media/thumb/{quote(name)}?v={store.mtime(name)}"

    def display_url(name: str) -> str:
        # Auflösung gehört zur Adresse: ändert sie sich, lädt der Player die neuen Versionen
        w, h = display_size()
        return f"/media/display/{quote(name)}?v={store.mtime(name)}&s={w}x{h}"

    templates.env.globals.update(
        thumb_url=thumb_url,
        KINDS=sched.KINDS,
        WEEKDAYS_SHORT=sched.WEEKDAYS_SHORT,
        WEEKDAYS_LONG=sched.WEEKDAYS_LONG,
        NTHS=sched.NTHS,
    )
    def fmt_dt(d: datetime, long: bool = False) -> str:
        names = sched.WEEKDAYS_LONG if long else sched.WEEKDAYS_SHORT
        return f"{names[d.weekday()]}{',' if long else ''} {d:%d.%m.%Y %H:%M}"

    templates.env.filters["dt"] = fmt_dt
    templates.env.filters["mmss"] = lambda sec: f"{int(sec) // 60}:{int(sec) % 60:02d} min"
    templates.env.filters["bilder"] = lambda n: "1 Bild" if n == 1 else f"{n} Bilder"

    if warmup:
        store.prune_display_cache(*display_size())  # Reste früherer Auflösungen entfernen
        store.warmup_async(*display_size())

    # --- Hilfsfunktionen --------------------------------------------------
    def flash(request: Request, text: str, kind: str = "ok") -> None:
        request.session["flashes"] = request.session.get("flashes", []) + [{"text": text, "kind": kind}]

    def redirect(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def render(request: Request, name: str, status_code: int = 200, **ctx):
        ctx.update(
            logged_in=bool(request.session.get("auth")),
            flashes=request.session.pop("flashes", []),
            default_password=not db.settings()["password_hash"],
            path=request.url.path,
        )
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def require_login(request: Request) -> None:
        if not request.session.get("auth"):
            raise HTTPException(status_code=303, headers={"Location": f"/login?next={quote(request.url.path)}"})

    def get_schedule_or_404(schedule_id: int):
        row = db.schedule(schedule_id)
        if row is None:
            raise HTTPException(404, "Zeitraum nicht gefunden")
        return row

    def playlist_payload(pl: Playlist) -> dict:
        s = db.settings()
        payload = {
            "fit": s["fit"],
            "transition_ms": int(s["transition_ms"]),
            "fallback": pl.fallback,
            # Noch nichts eingerichtet: Player zeigt einen Hinweis statt Schwarz
            "setup_needed": not pl.slides and not pl.closed and (not store.list() or not db.schedules()),
            "blocks": [b.name for b in pl.blocks],
            "slides": [
                {"url": display_url(sl.filename), "duration": sl.duration, "block": b.name}
                for b in pl.blocks
                for sl in b.slides
            ],
        }
        payload["version"] = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
        payload["server_time"] = pl.at.isoformat(timespec="seconds")
        return payload

    def parse_at(value: str | None) -> datetime:
        if value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.now()

    # --- Öffentlich: Login, Player, API, Bilder ---------------------------
    @app.get("/")
    def index():
        return redirect("/admin")

    @app.get("/login")
    def login_form(request: Request, next: str = "/admin"):
        return render(request, "login.html", next=next)

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        target = str(form.get("next") or "/admin")
        if not target.startswith("/") or target.startswith("//"):
            target = "/admin"
        if auth.verify_password(str(form.get("password") or ""), db.settings()["password_hash"]):
            request.session["auth"] = True
            return redirect(target)
        flash(request, "Falsches Passwort.", "error")
        return redirect(f"/login?next={quote(target)}")

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return redirect("/login")

    @app.get("/player")
    def player(request: Request):
        return templates.TemplateResponse(request, "player.html", {"hostname": socket.gethostname()})

    @app.get("/api/playlist")
    def api_playlist(at: str | None = None):
        pl = build_playlist(db, store, parse_at(at))
        return JSONResponse(playlist_payload(pl), headers={"Cache-Control": "no-store"})

    @app.get("/media/display/{name}")
    def media_display(name: str):
        try:
            return FileResponse(store.display_path(name, *display_size()), media_type="image/jpeg")
        except ImageError:
            raise HTTPException(404)

    @app.get("/media/thumb/{name}")
    def media_thumb(name: str):
        try:
            return FileResponse(store.thumb_path(name), media_type="image/jpeg")
        except ImageError:
            raise HTTPException(404)

    # --- Admin ------------------------------------------------------------
    admin = APIRouter(prefix="/admin", dependencies=[Depends(require_login)])

    @admin.get("")
    def schedules_list(request: Request):
        now = datetime.now()
        current = build_playlist(db, store, now)
        items = []
        for row in db.schedules():
            rule = sched.Rule.from_row(row)
            slides = db.slides(row["id"])
            items.append({
                "row": row,
                "description": sched.describe(rule),
                "active": bool(row["enabled"]) and sched.is_active(rule, now),
                "upcoming": sched.upcoming(rule, now) if row["enabled"] else [],
                "slides": slides,
            })
        return render(request, "schedules.html", items=items, current=current, now=now)

    @admin.get("/schedules/new")
    def schedule_new(request: Request):
        return render(request, "schedule_edit.html", s=NEW_SCHEDULE, errors=[], slides=[], pool=[])

    @admin.post("/schedules/new")
    async def schedule_create(request: Request):
        values, errors = parse_schedule_form(await request.form())
        if errors:
            return render(request, "schedule_edit.html", status_code=400,
                          s={**NEW_SCHEDULE, **values}, errors=errors, slides=[], pool=[])
        new_id = db.create_schedule(values)
        flash(request, f"Zeitraum „{values['name']}“ angelegt. Jetzt Bilder hinzufügen.")
        return redirect(f"/admin/schedules/{new_id}#bilder")

    def edit_context(row) -> dict:
        settings = db.settings()
        rule = sched.Rule.from_row(row)
        slides = [s for s in db.slides(row["id"])]
        pool = store.list()
        default_duration = int(settings["default_duration"])
        return dict(
            slides=slides,
            missing={s["filename"] for s in slides} - set(pool),
            pool=pool,
            default_duration=default_duration,
            description=sched.describe(rule),
            upcoming=sched.upcoming(rule, datetime.now(), count=5),
            total=sum((s["duration"] or default_duration) for s in slides if s["filename"] in pool),
        )

    @admin.get("/schedules/{schedule_id}")
    def schedule_edit(request: Request, schedule_id: int):
        row = get_schedule_or_404(schedule_id)
        return render(request, "schedule_edit.html", s=dict(row), errors=[], **edit_context(row))

    @admin.post("/schedules/{schedule_id}")
    async def schedule_update(request: Request, schedule_id: int):
        row = get_schedule_or_404(schedule_id)
        values, errors = parse_schedule_form(await request.form())
        if errors:
            return render(request, "schedule_edit.html", status_code=400,
                          s={**dict(row), **values}, errors=errors, **edit_context(row))
        db.update_schedule(schedule_id, values)
        flash(request, "Zeitraum gespeichert.")
        return redirect(f"/admin/schedules/{schedule_id}")

    @admin.post("/schedules/{schedule_id}/delete")
    def schedule_delete(request: Request, schedule_id: int):
        row = get_schedule_or_404(schedule_id)
        db.delete_schedule(schedule_id)
        flash(request, f"Zeitraum „{row['name']}“ gelöscht.")
        return redirect("/admin")

    @admin.post("/schedules/{schedule_id}/duplicate")
    def schedule_duplicate(request: Request, schedule_id: int):
        get_schedule_or_404(schedule_id)
        new_id = db.duplicate_schedule(schedule_id)
        flash(request, "Kopie angelegt (noch deaktiviert).")
        return redirect(f"/admin/schedules/{new_id}")

    @admin.post("/schedules/{schedule_id}/toggle")
    def schedule_toggle(request: Request, schedule_id: int):
        row = get_schedule_or_404(schedule_id)
        db.set_enabled(schedule_id, not row["enabled"])
        return redirect("/admin")

    @admin.post("/schedules/{schedule_id}/slides/add")
    async def slides_add(request: Request, schedule_id: int):
        get_schedule_or_404(schedule_id)
        form = await request.form()
        pool = set(store.list())
        names = [n for n in form.getlist("filenames") if n in pool]
        if names:
            db.add_slides(schedule_id, names)
            flash(request, f"{len(names)} Bild(er) hinzugefügt.")
        return redirect(f"/admin/schedules/{schedule_id}#bilder")

    @admin.post("/schedules/{schedule_id}/slides/order")
    async def slides_order(request: Request, schedule_id: int):
        get_schedule_or_404(schedule_id)
        body = await request.json()
        ids = [i for i in (_int(x) for x in body.get("order", [])) if i is not None]
        db.reorder_slides(schedule_id, ids)
        return {"ok": True}

    @admin.post("/slides/{slide_id}/duration")
    async def slide_duration(request: Request, slide_id: int):
        if db.slide(slide_id) is None:
            raise HTTPException(404)
        body = await request.json()
        duration = _int(body.get("duration"))
        if duration is not None and not 1 <= duration <= 3600:
            return JSONResponse({"ok": False, "error": "1–3600 Sekunden"}, status_code=400)
        db.set_slide_duration(slide_id, duration)
        return {"ok": True}

    @admin.post("/slides/{slide_id}/delete")
    def slide_delete(request: Request, slide_id: int):
        slide = db.slide(slide_id)
        if slide is None:
            raise HTTPException(404)
        db.delete_slide(slide_id)
        return redirect(f"/admin/schedules/{slide['schedule_id']}#bilder")

    # --- Bilder-Pool --------------------------------------------------------
    @admin.get("/pool")
    def pool_view(request: Request):
        users = db.image_users()
        default_image = db.settings()["default_image"]
        images = [{"name": n, "users": users.get(n, []), "is_default": n == default_image} for n in store.list()]
        return render(request, "pool.html", images=images, pool_dir=store.pool_dir)

    @admin.get("/pool/info/{name}")
    def pool_info(name: str):
        try:
            info = store.info(name)
        except ImageError:
            raise HTTPException(404)
        info.update(
            users=db.image_users().get(name, []),
            is_default=db.settings()["default_image"] == name,
            display_url=display_url(name),
        )
        return info

    @admin.post("/pool/upload")
    async def pool_upload(request: Request):
        """Nimmt eine oder mehrere Dateien an.

        Mit ``Accept: application/json`` (Upload per JavaScript mit Fortschritt)
        kommt eine JSON-Antwort, sonst eine Weiterleitung mit Meldungen.
        """
        form = await request.form()
        allow_duplicate = bool(form.get("allow_duplicate"))
        saved, duplicates, errors = [], [], []
        for f in form.getlist("files"):
            if not getattr(f, "filename", None):
                continue
            try:
                name = await run_in_threadpool(store.save_upload, f.filename, f.file, allow_duplicate)
                await run_in_threadpool(store.thumb_path, name)
                saved.append(name)
            except DuplicateImage as exc:
                duplicates.append({"filename": exc.filename, "existing": exc.existing})
            except ImageError as exc:
                errors.append(str(exc))
        if saved:
            store.warmup_async(*display_size())

        if "application/json" in request.headers.get("accept", ""):
            return {"saved": saved, "duplicates": duplicates, "errors": errors}
        if saved:
            flash(request, f"{len(saved)} Bild(er) hochgeladen.")
        for d in duplicates:
            flash(request, f"{d['filename']}: bereits im Pool als „{d['existing']}“, übersprungen.", "warn")
        for e in errors:
            flash(request, e, "error")
        return redirect("/admin/pool")

    @admin.post("/pool/delete")
    async def pool_delete(request: Request):
        names = [str(n) for n in (await request.form()).getlist("name") if n]
        deleted = []
        for name in names:
            try:
                store.delete(name)
            except ImageError as exc:
                flash(request, str(exc), "error")
                continue
            db.remove_image_everywhere(name)
            if db.settings()["default_image"] == name:
                db.set_settings(default_image="")
            deleted.append(name)
        if len(deleted) == 1:
            flash(request, f"„{deleted[0]}“ gelöscht.")
        elif deleted:
            flash(request, f"{len(deleted)} Bilder gelöscht.")
        return redirect("/admin/pool")

    @admin.post("/pool/rename")
    async def pool_rename(request: Request):
        form = await request.form()
        old, new = str(form.get("name") or ""), str(form.get("new_name") or "")
        try:
            target = store.rename(old, new)
        except ImageError as exc:
            flash(request, str(exc), "error")
            return redirect("/admin/pool")
        if target != old:
            db.rename_image(old, target)
            flash(request, f"„{old}“ umbenannt in „{target}“.")
        return redirect("/admin/pool")

    @admin.post("/pool/default")
    async def pool_default(request: Request):
        name = str((await request.form()).get("name") or "")
        if name and not store.exists(name):
            raise HTTPException(404)
        db.set_settings(default_image=name)
        flash(request, f"„{name}“ ist jetzt das Standardbild." if name else "Kein Standardbild mehr gesetzt.")
        return redirect("/admin/pool")

    # --- Vorschau -----------------------------------------------------------
    def day_timeline(day) -> list[dict]:
        """Pro aktiviertem Zeitraum die aktiven Abschnitte des Tages (für die Zeitleiste)."""
        rows = []
        for row in db.schedules():
            if not row["enabled"]:
                continue
            rule = sched.Rule.from_row(row)
            rows.append({
                "id": row["id"],
                "name": row["name"],
                "priority": row["priority"],
                "exclusive": bool(row["exclusive"]),
                "description": sched.describe(rule),
                "segments": sched.active_segments(rule, day),
                "slides": len(db.slides(row["id"])),
            })
        return rows

    @admin.get("/preview")
    def preview(request: Request, at: str | None = None):
        when = parse_at(at).replace(second=0, microsecond=0)
        timeline = day_timeline(when.date())
        return render(
            request, "preview.html",
            pl=build_playlist(db, store, when), when=when, timeline=timeline,
            prev_day=when - timedelta(days=1), next_day=when + timedelta(days=1),
        )

    @admin.get("/preview/change")
    def preview_change(at: str | None = None, direction: int = 1):
        """Nächster/vorheriger Wechsel der Playlist, auch über Tagesgrenzen hinweg."""
        rules = [sched.Rule.from_row(r) for r in db.schedules() if r["enabled"]]
        found = sched.find_change(rules, parse_at(at), 1 if direction >= 0 else -1)
        return {"at": found.strftime("%Y-%m-%dT%H:%M") if found else None}

    @admin.get("/preview/result")
    def preview_result(request: Request, at: str | None = None):
        """Nur der Ergebnis-Teil der Vorschau; wird beim Verschieben der Zeitleiste nachgeladen."""
        when = parse_at(at).replace(second=0, microsecond=0)
        return templates.TemplateResponse(
            request, "_preview_result.html", {"pl": build_playlist(db, store, when), "when": when}
        )

    # --- Dokumentation ------------------------------------------------------
    @admin.get("/docs")
    def docs(request: Request):
        return render(request, "docs.html")

    # --- Einstellungen ------------------------------------------------------
    @admin.get("/settings")
    def settings_view(request: Request):
        return render(request, "settings.html", settings=db.settings(), pool=store.list())

    @admin.post("/settings")
    async def settings_save(request: Request):
        form = await request.form()
        old = db.settings()
        errors = []

        def bounded(key, lo, hi, label):
            v = _int(form.get(key))
            if v is None or not lo <= v <= hi:
                errors.append(f"{label}: Wert zwischen {lo} und {hi} angeben.")
                return old[key]
            return str(v)

        new = {
            "default_duration": bounded("default_duration", 1, 3600, "Anzeigedauer"),
            "transition_ms": bounded("transition_ms", 0, 5000, "Überblendung"),
            "display_width": bounded("display_width", 320, 3840, "Breite"),
            "display_height": bounded("display_height", 240, 2160, "Höhe"),
            "fit": form.get("fit") if form.get("fit") in ("contain", "cover") else "contain",
            "default_image": str(form.get("default_image") or ""),
        }
        if new["default_image"] and not store.exists(new["default_image"]):
            new["default_image"] = ""
        db.set_settings(**new)
        for e in errors:
            flash(request, e, "error")
        if not errors:
            flash(request, "Einstellungen gespeichert.")
        if (new["display_width"], new["display_height"]) != (old["display_width"], old["display_height"]):
            store.prune_display_cache(*display_size())
            store.warmup_async(*display_size())
            flash(request, "Neue Auflösung: Die Bilder werden im Hintergrund neu berechnet. "
                           "Der Player übernimmt sie beim nächsten Bildwechsel.", "warn")
        return redirect("/admin/settings")

    @admin.post("/password")
    async def password_change(request: Request):
        form = await request.form()
        current, new, repeat = (str(form.get(k) or "") for k in ("current", "new", "repeat"))
        if not auth.verify_password(current, db.settings()["password_hash"]):
            flash(request, "Aktuelles Passwort ist falsch.", "error")
        elif len(new) < 6:
            flash(request, "Das neue Passwort muss mindestens 6 Zeichen haben.", "error")
        elif new != repeat:
            flash(request, "Die Wiederholung stimmt nicht überein.", "error")
        else:
            db.set_settings(password_hash=auth.hash_password(new))
            flash(request, "Passwort geändert.")
        return redirect("/admin/settings#password")

    app.include_router(admin)
    return app
