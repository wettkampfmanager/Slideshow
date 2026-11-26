import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from slideshow.app import create_app


def png(color="red", size=(800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    c = TestClient(create_app(tmp_path, warmup=False))
    r = c.post("/login", data={"password": "slideshow"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    return c


def make_schedule(client, **kw):
    data = dict(name="Test", priority="10", kind="daily", start_time="00:00", end_time="23:59", enabled="1")
    data.update(kw)
    r = client.post("/admin/schedules/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("/")[-1].split("#")[0])


_counter = iter(range(1, 10_000))


def upload(client, *names):
    # jedes Bild mit eigenem Inhalt, sonst greift die Duplikat-Erkennung
    files = [("files", (n, png(color=(next(_counter) % 256, 80, 120)), "image/png")) for n in names]
    assert client.post("/admin/pool/upload", files=files).status_code == 200


def test_admin_requires_login(tmp_path):
    c = TestClient(create_app(tmp_path, warmup=False))
    r = c.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.post("/login", data={"password": "falsch"}, follow_redirects=False).headers["location"].startswith("/login")


def test_pages_render(client):
    sid = make_schedule(client)
    for url in ["/admin", "/admin/pool", "/admin/preview", "/admin/settings", "/admin/docs", f"/admin/schedules/{sid}",
                "/admin/schedules/new", "/player"]:
        assert client.get(url).status_code == 200, url


def test_blockwise_by_priority_with_order_and_durations(client):
    upload(client, "a.png", "b.png", "c.png")
    low = make_schedule(client, name="Montags", priority="20", kind="weekly", weekdays="0")
    high = make_schedule(client, name="Mittag", priority="5", start_time="12:00", end_time="13:45")
    client.post(f"/admin/schedules/{low}/slides/add", data={"filenames": ["c.png"]})
    client.post(f"/admin/schedules/{high}/slides/add", data={"filenames": ["a.png", "b.png"]})

    slides = client.get(f"/admin/schedules/{high}").text  # Seite rendert mit Slides
    assert "a.png" in slides

    # Reihenfolge umdrehen und Dauer setzen
    db = client.app.state.db
    ids = [s["id"] for s in db.slides(high)]
    assert client.post(f"/admin/schedules/{high}/slides/order", json={"order": ids[::-1]}).json()["ok"]
    assert client.post(f"/admin/slides/{ids[0]}/duration", json={"duration": 5}).json()["ok"]
    assert client.post(f"/admin/slides/{ids[0]}/duration", json={"duration": 0}).status_code == 400

    p = client.get("/api/playlist", params={"at": "2026-11-30T12:30"}).json()  # Montag Mittag
    assert p["blocks"] == ["Mittag", "Montags"]
    assert [(s["url"].split("?")[0], s["duration"]) for s in p["slides"]] == [
        ("/media/display/b.png", 30), ("/media/display/a.png", 5), ("/media/display/c.png", 30)]

    p2 = client.get("/api/playlist", params={"at": "2026-11-30T15:00"}).json()
    assert p2["blocks"] == ["Montags"] and p2["version"] != p["version"]

    img = client.get(p["slides"][0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_fallback_default_image(client):
    upload(client, "standard.png")
    assert client.get("/api/playlist").json()["slides"] == []
    client.post("/admin/pool/default", data={"name": "standard.png"})
    p = client.get("/api/playlist").json()
    assert p["fallback"] and p["slides"][0]["url"].startswith("/media/display/standard.png")


def test_delete_image_removes_slides(client):
    upload(client, "x.png")
    sid = make_schedule(client)
    client.post(f"/admin/schedules/{sid}/slides/add", data={"filenames": ["x.png"]})
    client.post("/admin/pool/delete", data={"name": "x.png"})
    assert client.app.state.db.slides(sid) == []


def test_invalid_upload_and_path_traversal(client):
    r = client.post("/admin/pool/upload", files=[("files", ("evil.png", b"not an image", "image/png"))])
    assert "keine gültige Bilddatei" in r.text
    assert client.get("/media/display/..%2Fslideshow.db").status_code == 404
    assert client.post("/admin/pool/delete", data={"name": "../slideshow.db"}).status_code == 200
    assert (client.app.state.store.pool_dir.parent / "slideshow.db").exists()


def test_schedule_validation(client):
    r = client.post("/admin/schedules/new", data=dict(name="X", kind="weekly", start_time="10:00", end_time="11:00"))
    assert r.status_code == 400 and "Wochentag" in r.text


def test_password_change(client):
    client.post("/admin/password", data={"current": "slideshow", "new": "geheim123", "repeat": "geheim123"})
    client.post("/logout")
    assert client.post("/login", data={"password": "slideshow"}, follow_redirects=False).headers["location"].startswith("/login")
    assert client.post("/login", data={"password": "geheim123"}, follow_redirects=False).headers["location"] == "/admin"


# --- Bilder-Pool -------------------------------------------------------------

def test_duplicate_upload_is_skipped(client):
    data = png("blue")
    r = client.post("/admin/pool/upload", files=[("files", ("a.png", data, "image/png"))],
                    headers={"Accept": "application/json"}).json()
    assert r["saved"] == ["a.png"]
    r = client.post("/admin/pool/upload", files=[("files", ("kopie.png", data, "image/png"))],
                    headers={"Accept": "application/json"}).json()
    assert r["saved"] == [] and r["duplicates"] == [{"filename": "kopie.png", "existing": "a.png"}]
    # bewusst trotzdem hochladen
    r = client.post("/admin/pool/upload", files=[("files", ("kopie.png", data, "image/png"))],
                    data={"allow_duplicate": "1"}, headers={"Accept": "application/json"}).json()
    assert r["saved"] == ["kopie.png"]
    # gleicher Name, anderer Inhalt -> neuer Name
    r = client.post("/admin/pool/upload", files=[("files", ("a.png", png("green"), "image/png"))],
                    headers={"Accept": "application/json"}).json()
    assert r["saved"] == ["a-1.png"]


def test_duplicate_detects_files_copied_into_pool(client):
    store = client.app.state.store
    (store.pool_dir / "manuell.png").write_bytes(png("purple"))
    r = client.post("/admin/pool/upload", files=[("files", ("neu.png", png("purple"), "image/png"))],
                    headers={"Accept": "application/json"}).json()
    assert r["duplicates"][0]["existing"] == "manuell.png"


def test_bulk_delete(client):
    upload(client, "a.png", "b.png", "c.png")
    client.post("/admin/pool/delete", data={"name": ["a.png", "c.png"]})
    assert client.app.state.store.list() == ["b.png"]


def test_rename_keeps_assignments(client):
    upload(client, "alt.png")
    sid = make_schedule(client)
    client.post(f"/admin/schedules/{sid}/slides/add", data={"filenames": ["alt.png"]})
    client.post("/admin/pool/default", data={"name": "alt.png"})
    client.get("/media/thumb/alt.png")

    client.post("/admin/pool/rename", data={"name": "alt.png", "new_name": "Neuer Name"})  # Endung ergänzt
    db, store = client.app.state.db, client.app.state.store
    assert store.list() == ["Neuer Name.png"]
    assert [s["filename"] for s in db.slides(sid)] == ["Neuer Name.png"]
    assert db.settings()["default_image"] == "Neuer Name.png"
    assert client.get("/media/thumb/Neuer%20Name.png").status_code == 200

    upload(client, "x.png")
    r = client.post("/admin/pool/rename", data={"name": "x.png", "new_name": "Neuer Name.png"})
    assert "existiert bereits" in r.text
    r = client.post("/admin/pool/rename", data={"name": "x.png", "new_name": "../böse.png"})
    assert (store.pool_dir / "böse.png").exists()  # Pfadanteil wird entfernt


def test_info(client):
    client.post("/admin/pool/upload", files=[("files", ("i.png", png(size=(640, 480)), "image/png"))])
    sid = make_schedule(client, name="Mittag")
    client.post(f"/admin/schedules/{sid}/slides/add", data={"filenames": ["i.png"]})
    info = client.get("/admin/pool/info/i.png").json()
    assert (info["width"], info["height"], info["format"]) == (640, 480, "PNG")
    assert info["users"] == [{"id": sid, "name": "Mittag"}] and not info["is_default"]
    assert client.get("/admin/pool/info/fehlt.png").status_code == 404


def test_preview_timeline_and_result(client):
    upload(client, "a.png")
    sid = make_schedule(client, name="Mittag", start_time="12:00", end_time="13:45")
    client.post(f"/admin/schedules/{sid}/slides/add", data={"filenames": ["a.png"]})
    page = client.get("/admin/preview", params={"at": "2026-11-26T12:30"}).text
    assert "[[720, 825]]" in page and 'id="tl-scrub"' in page
    part = client.get("/admin/preview/result", params={"at": "2026-11-26T12:30"}).text
    assert "Mittag" in part and "<html" not in part
    assert "schwarz" in client.get("/admin/preview/result", params={"at": "2026-11-26T14:00"}).text


# --- Schließzeiten -----------------------------------------------------------

def test_closure_hides_all_other_schedules(client):
    upload(client, "normal.png", "zu.png", "inventur.png", "standard.png")
    client.post("/admin/pool/default", data={"name": "standard.png"})
    normal = make_schedule(client, name="Täglich", priority="1")
    client.post(f"/admin/schedules/{normal}/slides/add", data={"filenames": ["normal.png"]})

    r = client.post("/admin/schedules/new", data=dict(
        name="Weihnachten", kind="period", priority="10", exclusive="1", enabled="1",
        period_start="2026-12-23T14:00", period_end="2027-01-02T08:00"), follow_redirects=False)
    assert r.status_code == 303, r.text
    xmas = int(r.headers["location"].split("/")[-1].split("#")[0])
    row = client.app.state.db.schedule(xmas)
    assert (row["on_date"], row["start_time"], row["end_date"], row["end_time"], row["exclusive"]) == \
        ("2026-12-23", "14:00", "2027-01-02", "08:00", 1)

    def names(at):
        p = client.get("/api/playlist", params={"at": at}).json()
        return p["blocks"], [s["url"].split("?")[0].rsplit("/", 1)[1] for s in p["slides"]]

    assert names("2026-12-23T13:59") == (["Täglich"], ["normal.png"])
    # Schließzeit ohne Bilder -> schwarz, auch kein Standardbild
    assert names("2026-12-24T12:00") == (["Weihnachten"], [])
    client.post(f"/admin/schedules/{xmas}/slides/add", data={"filenames": ["zu.png"]})
    assert names("2026-12-24T12:00") == (["Weihnachten"], ["zu.png"])

    # Zweite, überlappende Schließzeit (wiederkehrend): beide laufen, nach Priorität
    inv = make_schedule(client, name="Inventur", priority="5", kind="weekly", weekdays="3", exclusive="1")
    client.post(f"/admin/schedules/{inv}/slides/add", data={"filenames": ["inventur.png"]})
    assert names("2026-12-24T12:00") == (["Inventur", "Weihnachten"], ["inventur.png", "zu.png"])  # Do
    assert names("2026-12-25T12:00") == (["Weihnachten"], ["zu.png"])                               # Fr
    assert names("2027-01-07T12:00") == (["Inventur"], ["inventur.png"])                            # Do, nach Weihnachten
    assert names("2027-01-08T12:00") == (["Täglich"], ["normal.png"])

    # Seiten zeigen die Schließzeit an
    assert "Schließzeit" in client.get("/admin").text
    assert "ausgeblendet" in client.get("/admin/preview/result", params={"at": "2026-12-24T12:00"}).text
    assert "2026-12-23T14:00" in client.get(f"/admin/schedules/{xmas}").text


def test_migration_adds_columns_to_old_database(tmp_path):
    import sqlite3
    from slideshow.db import Database
    old = sqlite3.connect(tmp_path / "alt.db")
    old.executescript("""CREATE TABLE schedules (id INTEGER PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 10, kind TEXT NOT NULL, weekdays TEXT NOT NULL DEFAULT '', nths TEXT NOT NULL DEFAULT '',
        monthday INTEGER, on_date TEXT, start_time TEXT NOT NULL, end_time TEXT NOT NULL, valid_from TEXT, valid_until TEXT);
        INSERT INTO schedules(name, kind, start_time, end_time) VALUES('Alt', 'daily', '00:00', '23:59');""")
    old.commit(); old.close()
    db = Database(tmp_path / "alt.db")
    row = db.schedules()[0]
    assert row["name"] == "Alt" and row["exclusive"] == 0 and row["end_date"] is None


def test_preview_change_endpoint(client):
    make_schedule(client, name="Montags", kind="weekly", weekdays="0")
    r = client.get("/admin/preview/change", params={"at": "2026-11-26T15:00", "direction": 1}).json()
    assert r == {"at": "2026-11-30T00:00"}
    r = client.get("/admin/preview/change", params={"at": "2026-11-26T15:00", "direction": -1}).json()
    assert r == {"at": "2026-11-24T00:00"}


def test_resolution_change_rebuilds_and_prunes_cache(client):
    upload(client, "a.png")
    sid = make_schedule(client)
    client.post(f"/admin/schedules/{sid}/slides/add", data={"filenames": ["a.png"]})
    store = client.app.state.store

    p1 = client.get("/api/playlist").json()
    url1 = p1["slides"][0]["url"]
    assert "s=1920x1080" in url1
    client.get(url1)
    assert (store.cache_dir / "display_1920x1080" / "a.png.jpg").exists()

    settings = dict(default_duration="30", transition_ms="1000", fit="contain", default_image="",
                    display_width="1280", display_height="720")
    client.post("/admin/settings", data=settings)

    p2 = client.get("/api/playlist").json()
    assert p2["version"] != p1["version"]            # Player bemerkt die Änderung
    assert "s=1280x720" in p2["slides"][0]["url"]
    assert not (store.cache_dir / "display_1920x1080").exists()  # alte Auflösung entfernt
    from PIL import Image
    img = Image.open(io.BytesIO(client.get(p2["slides"][0]["url"]).content))
    assert max(img.size) <= 1280 and img.size[1] <= 720
    assert (store.cache_dir / "thumbs" / "a.png.jpg").exists()   # Vorschaubilder bleiben
