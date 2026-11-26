# Slideshow

Zeitgesteuerte Bild-Slideshow für den Raspberry Pi. Je nach Uhrzeit, Wochentag oder
Datum laufen unterschiedliche Bilder. Bedient wird sie über eine Web-Oberfläche, die
von jedem PC, Tablet oder Handy im selben Netz erreichbar ist.

## Funktionen

- **Zeiträume** mit Wiederholung: täglich, an Wochentagen, am n-ten Wochentag im Monat
  (z. B. „erster Montag“, „letzter Freitag“), an einem festen Monatstag oder einmalig.
  Dazu Uhrzeit von/bis (auch über Mitternacht) und optional ein Gültigkeitszeitraum.
- **Überlappungen**: Alle aktiven Zeiträume laufen nacheinander, **blockweise nach
  Priorität** (kleinere Zahl zuerst). Innerhalb eines Blocks gilt die eigene Reihenfolge.
- **Reihenfolge** per Drag & Drop oder mit ▲▼, **Anzeigedauer** pro Bild (Standard: 30 s,
  in den Einstellungen änderbar).
- **Schließzeiten**: Ein Zeitraum kann als Schließzeit markiert werden. Solange eine
  Schließzeit aktiv ist, werden alle normalen Zeiträume ignoriert; es laufen nur die
  Bilder der Schließzeit(en), ohne Bilder bleibt der Bildschirm schwarz. Mit der Art
  „Zeitspanne“ geht das über mehrere Tage (z. B. 23.12. 14:00 bis 02.01. 08:00),
  wiederkehrend über die übrigen Arten (z. B. jeden Sonntag). Mehrere Schließzeiten
  sind möglich.
- **Standardbild**, wenn kein Zeitraum aktiv ist (sonst bleibt der Bildschirm schwarz).
- **Bilder-Pool**: Upload im Browser oder Dateien direkt nach `data/pool/` kopieren.
  Bilder werden einmalig auf Bildschirmgröße verkleinert, damit der Pi 3 flüssig bleibt.
- **Vorschau**: Zeigt, was zu einem beliebigen Zeitpunkt laufen würde.
- Änderungen erscheinen ohne Neustart auf dem Bildschirm (spätestens nach ca. 15 s
  und dem aktuellen Bild).

## Beispiele

| Anforderung                        | Einstellung                                                        |
|------------------------------------|--------------------------------------------------------------------|
| täglich 12:00–13:45                | Täglich, von 12:00 bis 13:45                                       |
| immer montags ganztägig            | Wöchentlich, Mo, „Ganztägig“ (00:00–23:59)                         |
| jeden ersten Montag 10:00–15:00    | Monatlich (n-ter Wochentag), 1., Mo, von 10:00 bis 15:00           |

## Installation auf dem Raspberry Pi

Voraussetzung: **Raspberry Pi OS mit Desktop, 64 Bit** (läuft auf dem Pi 3), Netzwerk
und korrekte Zeitzone (`sudo raspi-config` → Localisation → Timezone). Der Pi 3 hat
keine Echtzeituhr und braucht für die richtige Uhrzeit eine Netzwerkverbindung (NTP).

```bash
git clone <repo-url> ~/slideshow
cd ~/slideshow
sudo ./deploy/install.sh
sudo reboot
```

Das Skript
- installiert Chromium und die Python-Abhängigkeiten in `.venv`,
- richtet den Dienst `slideshow.service` ein (Server auf Port 8000, startet automatisch),
- startet nach dem Desktop-Login Chromium im Vollbild mit dem Player (`deploy/kiosk.sh`),
- aktiviert den Desktop-Autologin und schaltet die Bildschirmabschaltung aus.

Danach im Browser eines anderen Rechners öffnen:
`http://<hostname>.local:8000/admin` oder `http://<IP-des-Pi>:8000/admin`.
Standardpasswort: **`slideshow`**, bitte gleich unter *Einstellungen* ändern.

Tipp: Im Router dem Pi eine feste IP zuweisen (DHCP-Reservierung).

### Updates vom Entwicklungsrechner einspielen

```bash
./deploy/deploy.sh                    # Standardziel bem@raspizerobem.local
./deploy/deploy.sh bem@192.168.1.50   # anderes Ziel (oder DEPLOY_TARGET setzen)
./deploy/deploy.sh --dry-run          # nur anzeigen, was übertragen würde
```

Das Skript führt die Tests aus, überträgt den Code per rsync nach `~/slideshow`,
installiert Pakete nur bei geänderter `requirements.txt` und startet den Dienst neu.
Beim ersten Aufruf richtet es Python-Umgebung und Dienst selbst ein (nur Server,
ohne Kiosk-Player). Der Ordner `data/` auf dem Pi wird nie angefasst.
In VS Code: *Terminal → Task ausführen → Deploy auf Pi*.

Voraussetzung: SSH-Zugang per Schlüssel und `sudo` ohne Passwort (Standard bei
Raspberry Pi OS).

### Nützliche Befehle

```bash
sudo systemctl status slideshow      # läuft der Server?
journalctl -u slideshow -f           # Server-Log
sudo systemctl restart slideshow     # Server neu starten
```

Falls der Player nicht automatisch startet: `deploy/kiosk.sh` im Autostart der
Desktop-Umgebung eintragen.

## Entwicklung

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn --factory slideshow.app:create_app --reload --host 0.0.0.0 --port 8000
.venv/bin/python -m pytest
```

Daten liegen in `data/` (SQLite-Datenbank, Pool, Cache). Andere Orte lassen sich über
die Umgebungsvariablen `SLIDESHOW_DATA` und `SLIDESHOW_POOL` setzen.
**Backup**: den Ordner `data/` sichern (`data/cache/` kann weggelassen werden).

## Aufbau

```
slideshow/
  schedule.py    Zeitregeln: wann ist ein Zeitraum aktiv?
  playlist.py    welche Bilder laufen jetzt, in welcher Reihenfolge
  db.py          SQLite-Speicher
  images.py      Pool, Upload, verkleinerte Anzeige- und Vorschaubilder
  app.py         Web-App (FastAPI): /admin, /player, /api/playlist
  templates/     HTML-Seiten
  static/        CSS, Admin-JS, Player-JS
deploy/          Installation, systemd-Dienst, Kiosk-Start
tests/
```
