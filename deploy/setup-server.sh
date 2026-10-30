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

echo "==> Pakete"
if command -v dnf >/dev/null; then
  # Oracle Linux / RHEL-Familie
  sudo dnf install -y -q git python3 python3-pip >/dev/null
  FIREWALL=firewalld
else
  # Ubuntu / Debian
  sudo apt-get update -qq
  sudo apt-get install -y -qq git python3 python3-venv python3-pip ufw >/dev/null
  FIREWALL=ufw
fi

echo "==> Code nach $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  sudo git -C "$APP_DIR" pull -q
else
  sudo git clone -q "$REPO" "$APP_DIR"
fi
sudo chown -R "$USER":"$USER" "$APP_DIR"

echo "==> Python-Umgebung"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "==> $APP_DIR/.env angelegt (aus .env.example). Bitte Werte eintragen."
fi

echo "==> systemd-Dienst"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=Slack Relay (Claude x Chatty)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python relay.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable -q $SERVICE

echo "==> Firewall: SSH + Webansicht (Port 8080)"
if [ "$FIREWALL" = firewalld ]; then
  sudo firewall-cmd --permanent --add-port=8080/tcp >/dev/null 2>&1 || true
  sudo firewall-cmd --reload >/dev/null 2>&1 || true
else
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw allow 8080/tcp >/dev/null
  sudo ufw --force enable >/dev/null
  # Oracle-Ubuntu-Images haben zusätzlich iptables-Regeln, die 8080 blocken:
  if command -v iptables >/dev/null; then
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
    sudo netfilter-persistent save 2>/dev/null || true
  fi
fi

echo "==> Dienst starten (läuft ohne .env-Werte noch nicht, startet nach Eintrag automatisch neu)"
sudo systemctl restart $SERVICE || true

echo
echo "Fertig. Nächste Schritte:"
echo "  1. nano $APP_DIR/.env       (Tokens und Keys eintragen)"
echo "  2. sudo systemctl restart $SERVICE"
echo "  3. journalctl -u $SERVICE -f   (Log; 'Relay startet' muss erscheinen)"
echo
echo "Update später: cd $APP_DIR && git pull && sudo systemctl restart $SERVICE"
