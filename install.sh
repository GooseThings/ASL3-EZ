#!/bin/bash
# ASL3-EZ - Installer
# Run as root: sudo bash install.sh

set -e

INSTALL_DIR="/opt/ASL3-EZ"
SERVICE_NAME="ASL3-EZ"
PORT="${PORT:-5000}"

echo ""
echo "============================================"
echo "  ASL3-EZ rpt.conf Editor - Installer"
echo "  by N8GMZ"
echo "============================================"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Please run as root: sudo bash install.sh"
  exit 1
fi

# Check Python3
if ! command -v python3 &>/dev/null; then
  echo "Installing python3..."
  apt install -y python3 python3-pip python3-venv python3-full
fi

echo "[1/6] Installing dependencies..."
apt install -y python3-venv python3-full 2>/dev/null || true

echo "[2/6] Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"

echo "[3/6] Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet flask gunicorn

echo "[4/6] Installing systemd service..."
cp "$INSTALL_DIR/ASL3-EZ.service" /etc/systemd/system/
systemctl daemon-reload

echo "[5/6] Opening firewall port $PORT..."
if command -v firewall-cmd &>/dev/null; then
  firewall-cmd --permanent --add-port=${PORT}/tcp 2>/dev/null && firewall-cmd --reload 2>/dev/null || true
  echo "  firewalld: port $PORT opened."
elif command -v ufw &>/dev/null; then
  ufw allow ${PORT}/tcp 2>/dev/null || true
  echo "  ufw: port $PORT opened."
else
  echo "  No firewall manager found - you may need to open port $PORT manually."
fi

echo "[6/6] Enabling and starting service..."
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo ""
  echo "============================================"
  echo "  ✅ Installation complete!"
  echo ""
  IP=$(hostname -I | awk '{print $1}')
  echo "  Open your browser and go to:"
  echo "  http://${IP}:${PORT}"
  echo ""
  echo "  rpt.conf:  /etc/asterisk/rpt.conf"
  echo "  Backups:   /etc/asterisk/rpt_backups/"
  echo "  Logs:      journalctl -u $SERVICE_NAME -f"
  echo "============================================"
else
  echo ""
  echo "WARNING: Service may not have started. Check:"
  echo "  journalctl -u $SERVICE_NAME -n 30"
fi
