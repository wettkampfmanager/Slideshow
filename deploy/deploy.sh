#!/usr/bin/env bash
# Überträgt den aktuellen Stand vom Entwicklungsrechner auf den Raspberry Pi
# und startet den Server dort neu.
#
#   ./deploy/deploy.sh                    # Ziel aus $DEPLOY_TARGET oder Standard
#   ./deploy/deploy.sh bem@192.168.1.50   # anderes Ziel
#   ./deploy/deploy.sh --dry-run          # nur anzeigen, was übertragen würde
#   ./deploy/deploy.sh --skip-tests       # Tests vorher nicht ausführen
#
# Beim ersten Mal wird auf dem Pi automatisch die Python-Umgebung und der
# systemd-Dienst "slideshow" eingerichtet (benötigt sudo ohne Passwort oder
# eine Passworteingabe). Chromium/Kiosk wird NICHT eingerichtet, dafür auf dem
# Pi einmalig deploy/install.sh ausführen.
#
# Daten auf dem Pi (data/: Bilder, Datenbank, Einstellungen) werden nie
# überschrieben oder gelöscht.
set -euo pipefail

TARGET="${DEPLOY_TARGET:-bem@raspizerobem.local}"
REMOTE_DIR="${DEPLOY_DIR:-slideshow}"   # relativ zum Home-Verzeichnis auf dem Pi
PORT="${DEPLOY_PORT:-8000}"
DRY_RUN=0
RUN_TESTS=1

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --skip-tests) RUN_TESTS=0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

cd "$(dirname "$0")/.."
HOST="${TARGET#*@}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET")
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "Ziel: $TARGET:~/$REMOTE_DIR"
"${SSH[@]}" true || { echo "SSH-Verbindung zu $TARGET fehlgeschlagen." >&2; exit 1; }

if [[ $RUN_TESTS -eq 1 && $DRY_RUN -eq 0 ]]; then
  step "Tests"
  if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import pytest" 2>/dev/null; then
    .venv/bin/python -m pytest -q || { echo "Tests fehlgeschlagen, Abbruch. (--skip-tests zum Überspringen)" >&2; exit 1; }
  else
    echo "pytest nicht installiert, Tests übersprungen."
  fi
fi

step "Dateien übertragen"
RSYNC_OPTS=(-az --delete --itemize-changes
  --exclude .git/ --exclude .venv/ --exclude data/ --exclude .vscode/
  --exclude __pycache__/ --exclude .pytest_cache/ --exclude '*.pyc' --exclude .deployed)
[[ $DRY_RUN -eq 1 ]] && RSYNC_OPTS+=(--dry-run)
CHANGES="$(rsync "${RSYNC_OPTS[@]}" ./ "$TARGET:$REMOTE_DIR/" | grep -v '/$' || true)"
if [[ -n "$CHANGES" ]]; then echo "$CHANGES" | sed 's/^/  /'; else echo "  keine Änderungen"; fi
[[ $DRY_RUN -eq 1 ]] && { echo; echo "Testlauf, nichts geändert."; exit 0; }

# Versionsinfo für den Pi
VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unbekannt)"
[[ -n "$(git status --porcelain 2>/dev/null)" ]] && VERSION="$VERSION+lokale-Änderungen"
printf 'version=%s\ndeployed=%s\nfrom=%s\n' "$VERSION" "$(date -Iseconds)" "$(hostname)" \
  | "${SSH[@]}" "cat > $REMOTE_DIR/.deployed"

step "Python-Umgebung und Dienst"
"${SSH[@]}" bash -s -- "$REMOTE_DIR" "$PORT" <<'REMOTE'
set -euo pipefail
cd "$HOME/$1"
PORT="$2"
APP_DIR="$PWD"

if [[ ! -x .venv/bin/python ]]; then
  echo "  Erstinstallation: Python-Umgebung anlegen"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi

# Abhängigkeiten nur installieren, wenn sich requirements.txt geändert hat
NEW_SUM="$(sha256sum requirements.txt | cut -d' ' -f1)"
OLD_SUM="$(cat .venv/.requirements.sha256 2>/dev/null || true)"
if [[ "$NEW_SUM" != "$OLD_SUM" ]]; then
  echo "  requirements.txt geändert, installiere Pakete …"
  .venv/bin/pip install -q -r requirements.txt
  echo "$NEW_SUM" > .venv/.requirements.sha256
else
  echo "  Pakete aktuell"
fi

mkdir -p data/pool
UNIT=/etc/systemd/system/slideshow.service
WANT="$(sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@USER@#$USER#g" -e "s#@PORT@#$PORT#g" deploy/slideshow.service)"
if [[ ! -f "$UNIT" ]] || [[ "$(cat "$UNIT")" != "$WANT" ]]; then
  echo "  systemd-Dienst einrichten/aktualisieren"
  echo "$WANT" | sudo tee "$UNIT" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable slideshow.service >/dev/null 2>&1
fi

echo "  Dienst neu starten"
sudo systemctl restart slideshow.service
REMOTE

step "Prüfen"
for _ in $(seq 1 30); do
  if curl -fs -o /dev/null "http://$HOST:$PORT/player"; then
    echo "  Server antwortet ($VERSION)"
    echo
    echo "  Admin:  http://$HOST:$PORT/admin"
    echo "  Player: http://$HOST:$PORT/player"
    exit 0
  fi
  sleep 1
done
echo "Server antwortet nicht. Log auf dem Pi:" >&2
"${SSH[@]}" "journalctl -u slideshow --no-pager -n 30" >&2
exit 1
