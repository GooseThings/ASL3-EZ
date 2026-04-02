#!/bin/bash
# ASL3 rpt.conf Editor - Installer
# Run as root: sudo bash install.sh

set -e

INSTALL_DIR="/opt/ASL3-EZ"
SERVICE_NAME="ASL3-EZ"
PORT="${PORT:-5000}"

echo ""
echo "============================================"
echo "  N8GMZ ASL3 rpt.conf Editor - Installer"
echo "============================================"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Please run as root: sudo bash install.sh"
  exit 1
fi

# Check Python3
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install with: sudo apt install python3 python3-pip python3-venv"
  exit 1
fi

echo "[1/5] Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"

echo "[2/5] Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "[3/5] Installing systemd service..."
cp "$INSTALL_DIR/asl3-rpt-editor.service" /etc/systemd/system/
systemctl daemon-reload

echo "[4/5] Enabling and starting service..."
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo "[5/5] Checking status..."
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo ""
  echo "============================================"
  echo "  ✅ Installation complete!"
  echo ""
  # Get IP
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
