#!/usr/bin/env bash
# Startet Chromium im Vollbild-Kiosk-Modus mit dem Player.
# Wird beim Desktop-Login automatisch gestartet (siehe install.sh).
set -u
URL="${SLIDESHOW_URL:-http://localhost:8000/player}"

# Nur eine Instanz
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/slideshow-kiosk.lock"
flock -n 9 || exit 0

# Warten, bis der Server läuft
for _ in $(seq 1 90); do
  curl -fs -o /dev/null "$URL" && break
  sleep 2
done

BROWSER="$(command -v chromium || command -v chromium-browser)"
PROFILE="$HOME/.cache/slideshow-kiosk"

# Chromium bei Absturz neu starten
while true; do
  # "Chromium wurde nicht korrekt beendet"-Hinweis unterdrücken
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
    "$PROFILE/Default/Preferences" 2>/dev/null || true
  "$BROWSER" \
    --kiosk "$URL" \
    --user-data-dir="$PROFILE" \
    --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --disable-features=Translate \
    --check-for-update-interval=31536000 \
    --password-store=basic \
    --disk-cache-size=52428800
  sleep 3
done
