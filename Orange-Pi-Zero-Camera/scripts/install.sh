#!/usr/bin/env bash
# Install the Bottle Wi-Fi Vendo app as a systemd service on the Orange Pi Zero 3.
# Runs the app IN PLACE (from this repo), so no copying to /opt is required.
#
#   sudo bash scripts/install.sh            # install + enable (does not auto-start)
#   sudo RUN_USER=orangepi bash scripts/install.sh
#
# After install: create .env (MikroTik creds), then:  sudo systemctl start bottle-vendo
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="bottle-vendo"
UNIT="/etc/systemd/system/${SERVICE}.service"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
PY="${PYTHON:-python3}"

echo "== Bottle Vendo install =="
echo "app dir : $APP_DIR"
echo "run user: $RUN_USER"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo (needs to write $UNIT and manage systemd)." >&2
  exit 1
fi

# 1) virtualenv + dependencies
if [ ! -d "$APP_DIR/.venv" ]; then
  echo "-- creating venv"
  "$PY" -m venv "$APP_DIR/.venv"
fi
echo "-- installing requirements (this can take a while on ARM)"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# The ncnn wheel declares a dependency on the FULL opencv-python, which needs
# X11 libs (libxcb.so.1) absent on the Bookworm *server* image and would make
# `import cv2` fail. Force cv2 to come from the headless build only. (Both share
# the same cv2/ dir, so uninstall-then-reinstall, not just uninstall.)
echo "-- pinning headless OpenCV (ncnn pulls the GUI build otherwise)"
"$APP_DIR/.venv/bin/pip" uninstall -y opencv-python >/dev/null 2>&1 || true
"$APP_DIR/.venv/bin/pip" install --force-reinstall --no-deps opencv-python-headless

# 2) .env
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "-- created .env from template — EDIT IT with your MikroTik credentials"
fi

# 3) groups for camera + serial
usermod -aG video,dialout "$RUN_USER" || true

# 4) generate the systemd unit for this exact path/user
echo "-- writing $UNIT"
cat > "$UNIT" <<EOF
[Unit]
Description=AI Bottle Wi-Fi Vendo (Orange Pi Zero 3)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/main.py
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
AmbientCapabilities=CAP_NET_BIND_SERVICE
MemoryMax=700M
SupplementaryGroups=video dialout
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"

echo
echo "Installed and enabled (starts on boot)."
echo "Next:"
echo "  1) edit $APP_DIR/.env with MikroTik credentials"
echo "  2) place a validated model under models/production/<version>/ (scripts/deploy.py)"
echo "  3) sudo systemctl start $SERVICE"
echo "  logs: journalctl -u $SERVICE -f"
