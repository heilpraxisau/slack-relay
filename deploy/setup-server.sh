#!/usr/bin/env bash
# Einmalige Einrichtung des Relay auf einem frischen Ubuntu-Server
# (z. B. Oracle Cloud Always Free, ARM oder x86).
#
# Aufruf auf dem Server als ubuntu-User:
#   curl -fsSL https://raw.githubusercontent.com/heilpraxisau/slack-relay/main/deploy/setup-server.sh | bash
# oder nach git clone:
#   bash deploy/setup-server.sh
#
# Danach: /opt/slack-relay/.env ausfüllen und `sudo systemctl restart slack-relay`.

set -euo pipefail

REPO="https://github.com/heilpraxisau/slack-relay.git"
APP_DIR="/opt/slack-relay"
SERVICE="slack-relay"
RUN_USER="slackrelay"   # unprivilegierter Dienstnutzer (Hinweis von Chatty)
# Quelle: SRC_DIR gesetzt = lokale Kopie verwenden, sonst git clone
SRC_DIR="${SRC_DIR:-}"

echo "==> Pakete"
if command -v dnf >/dev/null; then
  # Oracle Linux / RHEL-Familie
  # dnf lädt große Paketlisten und sprengt auf 1-GB-Micro-Servern den RAM:
  # nur aufrufen, wenn wirklich etwas fehlt.
  MISSING=""
  for p in git python3 python3-pip; do rpm -q "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
  if [ -n "$MISSING" ]; then sudo dnf install -y -q $MISSING >/dev/null; fi
  FIREWALL=firewalld
else
  # Ubuntu / Debian
  sudo apt-get update -qq
  sudo apt-get install -y -qq git python3 python3-venv python3-pip ufw >/dev/null
  FIREWALL=ufw
fi

echo "==> Dienstnutzer $RUN_USER"
id "$RUN_USER" >/dev/null 2>&1 || sudo useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$RUN_USER"

echo "==> Code nach $APP_DIR"
if [ -n "$SRC_DIR" ]; then
  sudo mkdir -p "$APP_DIR"
  sudo rsync -a --delete --exclude .venv --exclude .env --exclude '.git' "$SRC_DIR"/ "$APP_DIR"/ 2>/dev/null \
    || sudo cp -r "$SRC_DIR"/. "$APP_DIR"/
elif [ -d "$APP_DIR/.git" ]; then
  sudo git -C "$APP_DIR" pull -q
else
  sudo git clone -q "$REPO" "$APP_DIR"
fi

echo "==> Python-Umgebung"
sudo python3 -m venv "$APP_DIR/.venv"
sudo "$APP_DIR/.venv/bin/pip" install -q --no-cache-dir -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "==> $APP_DIR/.env angelegt (aus .env.example). Bitte Werte eintragen."
fi
# Rechte: Code gehört root (nur lesbar), .env und State gehören dem Dienstnutzer
sudo chown -R root:"$RUN_USER" "$APP_DIR"
sudo chmod -R o-rwx "$APP_DIR"
sudo chown "$RUN_USER":"$RUN_USER" "$APP_DIR/.env"
sudo chmod 600 "$APP_DIR/.env"
sudo mkdir -p /var/lib/$SERVICE && sudo chown "$RUN_USER":"$RUN_USER" /var/lib/$SERVICE

echo "==> systemd-Dienst"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=Slack Relay (Claude x Chatty)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=RELAY_STATE_FILE=/var/lib/$SERVICE/state.json
ExecStart=$APP_DIR/.venv/bin/python relay.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/$SERVICE

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable -q $SERVICE

# Firewall: bewusst NICHTS öffnen. Slack Socket Mode ist eine ausgehende
# Verbindung, die Webansicht erreicht man per SSH-Tunnel (Chattys Hinweis):
#   ssh -i ~/.ssh/oracle_relay -L 8080:127.0.0.1:8080 opc@<IP>
#   dann http://localhost:8080 im Browser
# Die Webansicht lauscht nur auf 127.0.0.1 (RELAY_VIEW_BIND in .env).

echo "==> Dienst starten (läuft ohne .env-Werte noch nicht, startet nach Eintrag automatisch neu)"
sudo systemctl restart $SERVICE || true

echo
echo "Fertig. Nächste Schritte:"
echo "  1. sudo nano $APP_DIR/.env       (Tokens und Keys eintragen)"
echo "  2. sudo systemctl restart $SERVICE"
echo "  3. sudo journalctl -u $SERVICE -f   (Log; 'Relay startet' muss erscheinen)"
echo
echo "Webansicht: ssh -L 8080:127.0.0.1:8080 opc@<IP>  →  http://localhost:8080"
echo "Update später: bash deploy/setup-server.sh erneut ausführen"
