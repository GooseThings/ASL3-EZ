#!/bin/bash
# ASL3 rpt.conf Editor - Uninstaller

set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash uninstall.sh"
  exit 1
fi

echo "Stopping and disabling asl3-rpt-editor service..."
systemctl stop asl3-rpt-editor 2>/dev/null || true
systemctl disable asl3-rpt-editor 2>/dev/null || true
rm -f /etc/systemd/system/asl3-rpt-editor.service
systemctl daemon-reload

echo "Removing installation directory..."
rm -rf /opt/asl3-rpt-editor

echo ""
echo "✅ Uninstalled. Your rpt.conf and backups in /etc/asterisk/ were NOT removed."
