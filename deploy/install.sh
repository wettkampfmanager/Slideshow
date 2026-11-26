#!/usr/bin/env bash
# Installation auf dem Raspberry Pi (Raspberry Pi OS mit Desktop, 64 Bit empfohlen).
# Aufruf aus dem Projektordner:  sudo ./deploy/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Bitte mit sudo ausführen: sudo $0"; exit 1; fi

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${SUDO_USER:-pi}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
PORT="${PORT:-8000}"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "Hinweis: 32-Bit-System erkannt. Empfohlen ist Raspberry Pi OS 64 Bit (läuft auf dem Pi 3)."
  echo "Unter 32 Bit kann die Installation einzelner Python-Pakete sehr lange dauern."
fi

echo "==> Systempakete"
apt-get update
apt-get install -y python3-venv curl
apt-get install -y chromium || apt-get install -y chromium-browser

echo "==> Python-Umgebung"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data/pool"

echo "==> Server-Dienst"
sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@USER@#$APP_USER#g" -e "s#@PORT@#$PORT#g" \
  "$APP_DIR/deploy/slideshow.service" > /etc/systemd/system/slideshow.service
systemctl daemon-reload
systemctl enable --now slideshow.service

echo "==> Kiosk-Autostart"
chmod +x "$APP_DIR/deploy/kiosk.sh"
sudo -u "$APP_USER" mkdir -p "$APP_HOME/.config/autostart"
cat > "$APP_HOME/.config/autostart/slideshow-kiosk.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Slideshow Kiosk
Exec=env SLIDESHOW_URL=http://localhost:$PORT/player $APP_DIR/deploy/kiosk.sh
X-GNOME-Autostart-enabled=true
DESKTOP
chown "$APP_USER": "$APP_HOME/.config/autostart/slideshow-kiosk.desktop"

if command -v raspi-config >/dev/null; then
  echo "==> Desktop-Autologin aktivieren, Bildschirmschoner aus"
  raspi-config nonint do_boot_behaviour B4 || true
  raspi-config nonint do_blanking 1 || true
fi

IP="$(hostname -I | awk '{print $1}')"
cat <<INFO

Fertig.
  Admin-Oberfläche:  http://$(hostname).local:$PORT/admin   oder   http://$IP:$PORT/admin
  Standardpasswort:  slideshow   (bitte sofort unter Einstellungen ändern)
  Bilder-Ordner:     $APP_DIR/data/pool

Nach einem Neustart (sudo reboot) startet der Player automatisch im Vollbild.
INFO
