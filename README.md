# ASL3 rpt.conf Editor

A clean, browser-based web interface for editing your **AllStarLink 3** `rpt.conf` configuration file — with live field editing, automatic backups, and one-click Asterisk restart.

![ASL3 rpt.conf Editor](https://img.shields.io/badge/AllStarLink-3-green?style=for-the-badge&logo=radio)
![Python](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=for-the-badge)

---

## Features

- 📻 **All rpt.conf settings** organized into logical sections (Basic, ID, Timers, Telemetry, DTMF, Nodes, Audio, and more)
- 🔄 **Toggle settings on/off** (comments/uncomments lines without deleting them)
- 💾 **Automatic backups** — every save creates a timestamped backup in `/etc/asterisk/rpt_backups/`
- 🗂 **Backup browser** — view and restore any of the last 10 backups
- ⚡ **One-click Asterisk restart** or softer `module reload app_rpt.so`
- 📝 **Raw editor** — edit rpt.conf directly as text when needed
- 📊 **Dashboard** showing node summary and quick actions
- 🌙 **Dark theme** designed for terminal-comfortable sysadmins

---

## Requirements

- AllStarLink 3 (Debian-based)
- Python 3.8 or later
- `python3-venv` package
- Root/sudo access (needed to write `/etc/asterisk/rpt.conf` and restart Asterisk)

---

## Installation

### Quick Install (Recommended)

```bash
# Clone or download this repository
git clone https://github.com/YOUR_USERNAME/asl3-rpt-editor.git
cd asl3-rpt-editor

# Run the installer as root
sudo bash install.sh
```

The installer will:
1. Copy files to `/opt/asl3-rpt-editor/`
2. Create a Python virtual environment and install Flask
3. Install and start a systemd service that auto-starts on boot

Then open your browser and go to:
```
http://YOUR_NODE_IP:5000
```

### Manual Install (No systemd)

```bash
git clone https://github.com/YOUR_USERNAME/asl3-rpt-editor.git
cd asl3-rpt-editor

# Create virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run (as root so it can write rpt.conf and restart Asterisk)
sudo venv/bin/python app.py
```

---

## Usage

### Dashboard

The **Dashboard** gives you an at-a-glance view of your configured nodes with quick links to configure each one.

### General Settings

The **General** page lets you set the `[general]` stanza options introduced in ASL3, including node lookup method and DNS settings.

### Node Settings

Click any node in the left sidebar (e.g., **Node 1999**) to open the full settings editor for that node. Settings are organized into collapsible sections:

| Section | Settings |
|---|---|
| Basic Node Settings | rxchannel, duplex, linktolink, callerid, context |
| Station ID | idrecording, idtalkover, idtime, politeid |
| Timers | hangtime, althangtime, totime, sleeptime, link activity timers |
| Telemetry | telemdefault, telemdynamic, courtesy tones, ducking |
| DTMF Settings | funcchar, endchar, functions stanzas |
| Node Connections | extnodefile, nodenames, connect/disconnect scripts |
| Audio & Streaming | linkmongain, echolink gains, notch filter, streaming |
| Parrot / Echo Mode | parrot, parrottime |
| RX Toneburst | rxburstfreq, rxburstthreshold, rxbursttime |
| Archiving & Logging | archivedir, format, statpost settings |
| Miscellaneous | controlstates, scheduler, tail messages, voter, APRS, Elke, MDC, LiTZ |

### Toggling Settings On/Off

Each setting has a small toggle switch. When **off**, the setting is commented out in `rpt.conf` (prefixed with `;`) — it's preserved but inactive. When **on**, it's active.

### Saving

- Click **Save Changes** in the yellow bar that appears when you've modified settings
- A timestamped backup is automatically created before every save
- You can view and restore backups from the **Backups** page or sidebar

### Restarting Asterisk

- **Reload Module** — runs `asterisk -rx "module reload app_rpt.so"` — softer, faster, but not always sufficient
- **Restart Asterisk** — runs `systemctl restart asterisk` — full restart, briefly drops all connections

### Raw Editor

The **Raw Editor** lets you edit `rpt.conf` as plain text. This is useful for editing stanzas not covered by the GUI (like `[functions]`, `[telemetry]`, `[nodes]`, `[morse]`). A backup is still created on save.

---

## Configuration

Environment variables can be set in the systemd service file (`/etc/systemd/system/asl3-rpt-editor.service`) or passed directly:

| Variable | Default | Description |
|---|---|---|
| `RPT_CONF_PATH` | `/etc/asterisk/rpt.conf` | Path to your rpt.conf |
| `BACKUP_DIR` | `/etc/asterisk/rpt_backups` | Where backups are stored |
| `RESTART_CMD` | `systemctl restart asterisk` | Command to restart Asterisk |
| `PORT` | `5000` | Web server port |
| `HOST` | `0.0.0.0` | Bind address |
| `SECRET_KEY` | `asl3-editor-change-me` | Flask secret key (change this!) |

To change settings, edit the service file:

```bash
sudo systemctl edit asl3-rpt-editor
```

Or edit directly:
```bash
sudo nano /etc/systemd/system/asl3-rpt-editor.service
sudo systemctl daemon-reload
sudo systemctl restart asl3-rpt-editor
```

---

## Testing Without a Real ASL3 Node

A `sample-rpt.conf` is included. To test with it:

```bash
RPT_CONF_PATH=$(pwd)/sample-rpt.conf python app.py
```

Then open `http://localhost:5000` in your browser.

---

## Security Notes

⚠️ **This web app runs as root** (required to write `/etc/asterisk/rpt.conf` and restart Asterisk). Recommendations:

1. **Bind to localhost only** if you're using an SSH tunnel or reverse proxy:
   ```
   HOST=127.0.0.1
   ```

2. **Change the SECRET_KEY** in the service file to a long random string

3. **Use a firewall** to restrict access to port 5000 to trusted IPs only:
   ```bash
   sudo ufw allow from YOUR_ADMIN_IP to any port 5000
   ```

4. **Consider HTTPS** using nginx as a reverse proxy with a self-signed cert

This tool is designed for use on a private/home network. Do not expose it directly to the internet without authentication.

---

## Uninstall

```bash
sudo bash /opt/asl3-rpt-editor/uninstall.sh
```

Your `rpt.conf` and backups in `/etc/asterisk/` are **not** deleted.

---

## Troubleshooting

**Service won't start:**
```bash
journalctl -u asl3-rpt-editor -n 50
```

**Permission denied saving rpt.conf:**
- Make sure the service is running as root (check `User=root` in the service file)
- Check file permissions: `ls -la /etc/asterisk/rpt.conf`

**Asterisk restart fails:**
- Verify Asterisk is managed by systemd: `systemctl status asterisk`
- The service must run as root to call `systemctl restart asterisk`

**Can't reach web UI:**
- Check the service is running: `systemctl status asl3-rpt-editor`
- Check firewall: `sudo ufw status`
- Verify the port: `ss -tlnp | grep 5000`

---

## Project Structure

```
asl3-rpt-editor/
├── app.py                  # Flask application (backend)
├── templates/
│   └── index.html          # Single-page web UI
├── requirements.txt        # Python dependencies (Flask only)
├── asl3-rpt-editor.service # systemd service unit file
├── install.sh              # Automated installer
├── uninstall.sh            # Uninstaller
├── sample-rpt.conf         # Sample rpt.conf for testing
└── README.md               # This file
```

---

## License

MIT License — use freely, at your own risk. Not affiliated with AllStarLink, Inc.

---

## Contributing

Pull requests welcome! Areas for improvement:
- Authentication (login page)
- Support for editing `[functions]`, `[telemetry]`, `[nodes]` stanzas via GUI
- Multi-node support with per-node settings isolation
- Dark/light theme toggle
- Config validation before save

---

*73 de the author — happy linking!* 📻
