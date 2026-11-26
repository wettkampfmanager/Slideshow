"""Bilder-Pool: Dateien im Pool-Ordner plus vorberechnete Anzeige- und Vorschaubilder.

Der Pi 3 hat wenig RAM und eine langsame CPU. Deshalb werden Originale (z. B.
24-MP-Kamerafotos) einmalig auf Bildschirmauflösung herunterskaliert und als
JPEG im Cache abgelegt. Der Browser bekommt nur noch die kleinen Versionen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
THUMB_SIZE = (320, 180)

_lock = threading.Lock()
_hash_lock = threading.Lock()


class ImageError(Exception):
    pass


class DuplicateImage(ImageError):
    def __init__(self, filename: str, existing: str):
        super().__init__(f"{filename}: bereits im Pool als „{existing}“")
        self.filename = filename
        self.existing = existing


def _clean_name(filename: str) -> tuple[str, str]:
    """Zerlegt in (Stamm, Endung) und entfernt problematische Zeichen."""
    base = Path(filename).name
    stem, suffix = os.path.splitext(base)
    if suffix.lower() not in ALLOWED_SUFFIXES:
        raise ImageError(f"{base}: Dateityp nicht unterstützt")
    stem = re.sub(r"[^\w\-. ]", "_", stem).strip(" .") or "bild"
    return stem, suffix.lower()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class ImageStore:
    def __init__(self, pool_dir: Path, cache_dir: Path):
        self.pool_dir = pool_dir
        self.cache_dir = cache_dir
        pool_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._hash_file = cache_dir / "hashes.json"

    # --- Pool ----------------------------------------------------------
    def list(self) -> list[str]:
        names = [
            p.name
            for p in self.pool_dir.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in ALLOWED_SUFFIXES
        ]
        return sorted(names, key=str.casefold)

    def exists(self, name: str) -> bool:
        # Nur Namen zulassen, die wirklich im Pool liegen (schützt vor ../-Pfaden).
        return name in set(self.list())

    def path(self, name: str) -> Path:
        if not self.exists(name):
            raise ImageError(f"Bild nicht gefunden: {name}")
        return self.pool_dir / name

    def mtime(self, name: str) -> int:
        try:
            return int((self.pool_dir / name).stat().st_mtime)
        except OSError:
            return 0

    def _unique_name(self, filename: str) -> str:
        stem, suffix = _clean_name(filename)
        candidate = f"{stem}{suffix}"
        n = 1
        while (self.pool_dir / candidate).exists():
            candidate = f"{stem}-{n}{suffix}"
            n += 1
        return candidate

    # --- Prüfsummen (Doppelte erkennen) ---------------------------------
    def hashes(self) -> dict[str, str]:
        """Dateiname -> SHA-256. Zwischengespeichert, neu berechnet nur bei Änderung."""
        with _hash_lock:
            try:
                cache = json.loads(self._hash_file.read_text())
            except (OSError, ValueError):
                cache = {}
            result, fresh, changed = {}, {}, False
            for name in self.list():
                st = (self.pool_dir / name).stat()
                key = [st.st_size, st.st_mtime_ns]
                entry = cache.get(name)
                if entry and entry[:2] == key:
                    sha = entry[2]
                else:
                    sha = _sha256(self.pool_dir / name)
                    changed = True
                fresh[name] = key + [sha]
                result[name] = sha
            if changed or set(fresh) != set(cache):
                self._hash_file.write_text(json.dumps(fresh))
            return result

    def find_by_hash(self, sha: str) -> str | None:
        return next((n for n, h in self.hashes().items() if h == sha), None)

    def save_upload(self, filename: str, data: BinaryIO, allow_duplicate: bool = False) -> str:
        filename = filename or "bild.jpg"
        _clean_name(filename)  # Dateityp früh prüfen
        tmp = self.pool_dir / f".upload-{os.getpid()}-{threading.get_ident()}"
        try:
            h = hashlib.sha256()
            with open(tmp, "wb") as out:
                while chunk := data.read(1024 * 1024):
                    h.update(chunk)
                    out.write(chunk)
            try:
                with Image.open(tmp) as im:
                    im.verify()
            except Exception as exc:  # noqa: BLE001 – Pillow wirft viele Typen
                raise ImageError(f"{filename}: keine gültige Bilddatei") from exc
            if not allow_duplicate and (existing := self.find_by_hash(h.hexdigest())):
                raise DuplicateImage(filename, existing)
            name = self._unique_name(filename)
            os.replace(tmp, self.pool_dir / name)
        finally:
            tmp.unlink(missing_ok=True)
        return name

    def delete(self, name: str) -> None:
        self.path(name).unlink()
        for cached in self.cache_dir.glob(f"*/{name}.jpg"):
            cached.unlink(missing_ok=True)

    def rename(self, old: str, new: str) -> str:
        """Benennt um; fehlt die Endung im neuen Namen, wird die alte übernommen."""
        src = self.path(old)
        new = new.strip()
        if not os.path.splitext(new)[1].lower() in ALLOWED_SUFFIXES:
            new += src.suffix.lower()
        stem, suffix = _clean_name(new)
        target = f"{stem}{suffix}"
        if target == old:
            return old
        if (self.pool_dir / target).exists():
            raise ImageError(f"„{target}“ existiert bereits")
        os.rename(src, self.pool_dir / target)
        for cached in self.cache_dir.glob(f"*/{old}.jpg"):
            os.replace(cached, cached.with_name(f"{target}.jpg"))
        return target

    def info(self, name: str) -> dict:
        path = self.path(name)
        st = path.stat()
        result = {
            "name": name,
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M"),
            "width": None, "height": None, "format": None,
        }
        try:
            with Image.open(path) as im:  # liest nur den Dateikopf
                w, h = im.size
                if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # EXIF: um 90° gedreht
                    w, h = h, w
                result.update(width=w, height=h, format=im.format)
        except Exception:  # noqa: BLE001
            pass
        return result

    # --- Abgeleitete Bilder --------------------------------------------
    def display_path(self, name: str, width: int, height: int) -> Path:
        return self._derived(name, f"display_{width}x{height}", (width, height), quality=88)

    def thumb_path(self, name: str) -> Path:
        return self._derived(name, "thumbs", THUMB_SIZE, quality=80)

    def _derived(self, name: str, folder: str, size: tuple[int, int], quality: int) -> Path:
        src = self.path(name)
        dst = self.cache_dir / folder / f"{name}.jpg"
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return dst
        with _lock:
            if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                return dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            _render(src, dst, size, quality)
        return dst

    def warmup(self, width: int, height: int) -> None:
        for name in self.list():
            if self._target_size not in (None, (width, height)):
                return  # Auflösung wurde inzwischen geändert, dieser Durchlauf ist überholt
            try:
                self.thumb_path(name)
                self.display_path(name, width, height)
            except Exception:  # noqa: BLE001 – ein kaputtes Bild soll den Rest nicht aufhalten
                pass

    def warmup_async(self, width: int, height: int) -> None:
        self._target_size = (width, height)
        threading.Thread(target=self.warmup, args=(width, height), daemon=True).start()

    _target_size: tuple[int, int] | None = None

    def prune_display_cache(self, width: int, height: int) -> list[str]:
        """Löscht Anzeigebilder aller anderen Auflösungen. Gibt die entfernten Ordner zurück."""
        keep = f"display_{width}x{height}"
        removed = []
        for folder in self.cache_dir.iterdir():
            if folder.is_dir() and re.fullmatch(r"display_\d+x\d+", folder.name) and folder.name != keep:
                shutil.rmtree(folder, ignore_errors=True)
                removed.append(folder.name)
        return removed


def _render(src: Path, dst: Path, size: tuple[int, int], quality: int) -> None:
    with Image.open(src) as im:
        longest = max(size)
        im.draft("RGB", (longest, longest))  # schnelles JPEG-Vorskalieren, spart RAM
        im = ImageOps.exif_transpose(im)
        im.thumbnail(size, Image.LANCZOS)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            im = Image.new("RGB", rgba.size, (0, 0, 0))
            im.paste(rgba, mask=rgba.getchannel("A"))
        else:
            im = im.convert("RGB")
        tmp = dst.with_name(dst.name + ".tmp")
        im.save(tmp, "JPEG", quality=quality, optimize=True)
        os.replace(tmp, dst)
