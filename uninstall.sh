#!/bin/bash
# ASL3-EZ - Uninstaller
set -e

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash uninstall.sh"
    exit 1
fi

echo ""
echo "============================================"
echo "  ASL3-EZ Uninstaller"
echo "============================================"
echo ""

echo "Stopping and disabling ASL3-EZ service..."
systemctl stop    ASL3-EZ 2>/dev/null || true
systemctl disable ASL3-EZ 2>/dev/null || true
rm -f /etc/systemd/system/ASL3-EZ.service

# Also clean up old service name if present
systemctl stop    asl3-rpt-editor 2>/dev/null || true
systemctl disable asl3-rpt-editor 2>/dev/null || true
rm -f /etc/systemd/system/asl3-rpt-editor.service

systemctl daemon-reload

echo "Removing installation directory /opt/ASL3-EZ..."
rm -rf /opt/ASL3-EZ

echo ""
echo "Uninstall complete."
echo "Your rpt.conf and backups in /etc/asterisk/ were NOT removed."
echo ""
