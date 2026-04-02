# ASL3-EZ

**AllStarLink 3 Editor by N8GMZ**

A browser-based web interface for editing your AllStarLink 3 `rpt.conf` configuration file — with organized field editors, DTMF function/macro/scheduler editors, COP command reference, automatic backups, and one-click Asterisk restart.

![ASL3-EZ](https://img.shields.io/badge/AllStarLink-3-green?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?style=for-the-badge)

---

## Features

- **All rpt.conf settings** — every node stanza parameter organized into collapsible sections
- **Functions, Link Functions, Phone Functions** stanza editors — add/remove/edit DTMF commands with a table UI
- **Macro editor** — define and edit macros in the `[macro]` stanza
- **Scheduler** — set up cron-style scheduled macro execution in `[schedule]`
- **Telemetry stanza editor** — define courtesy tones and telemetry entries
- **Nodes stanza editor** — manage static node connection entries
- **COP Commands reference** — full table of all COP, iLink, and Status commands
- **Toggle settings on/off** — comment/uncomment lines without deleting them
- **Automatic backups** — every save creates a timestamped backup
- **Backup browser** — view and restore any of the last 10 backups with confirmation dialogs
- **One-click Asterisk restart** or softer module reload — both with confirmation dialog
- **Raw text editor** — edit rpt.conf directly when needed
- **About page** with contact information

---

## Requirements

- AllStarLink 3 (Debian-based — Raspberry Pi or x86)
- Python 3.8 or later
- `python3-venv` and `python3-full` packages
- Root/sudo access (needed to write `/etc/asterisk/rpt.conf` and restart Asterisk)

---

## Installation

```bash
# Clone the repository
git clone https://github.com/GooseThings/ASL3-EZ.git
cd ASL3-EZ

# Run the installer as root
sudo bash install.sh
```

The installer automatically:
1. Installs `python3-venv` and `python3-full` (fixes the "externally managed environment" error)
2. Copies files to `/opt/ASL3-EZ/`
3. Creates a Python virtual environment and installs Flask + Gunicorn
4. Installs and starts a systemd service that auto-starts on boot
5. Opens port 5000 in the firewall (supports both `firewalld` and `ufw`)

Then open your browser:
```
http://YOUR_NODE_IP:5000
```

---

## Upgrading

To upgrade to a newer version:

```bash
cd ASL3-EZ
git pull
sudo bash install.sh
```

The installer will overwrite the previous installation and restart the service.

---

## Usage

### Dashboard
Overview of configured nodes with quick-action buttons.

### General Settings
Edit the `[general]` stanza — node lookup method and DNS settings (ASL3-specific).

### Node Settings
Click any node in the sidebar to open the full settings editor. Sections include:

| Section | Key Settings |
|---|---|
| Basic Node Settings | rxchannel, duplex, linktolink, callerid, context, startup_macro |
| Station ID | idrecording, idtalkover, idtime, politeid, beaconing |
| Timers | hangtime, althangtime, totime, sleeptime, link activity timers, remote timeouts |
| Telemetry Settings | telemdefault, telemdynamic, courtesy tones, ducking levels |
| DTMF Settings | funcchar, endchar, functions stanzas, dtmfkey, inxlat/outxlat |
| Node Connections | extnodefile, nodenames, connect/disconnect scripts, stanza pointers |
| Audio & Streaming | linkmongain, EchoLink gains, notch filter, streaming command |
| EchoLink & GUI Linking | eannmode, echolink/gui/phone/tlb telemetry defaults |
| Parrot / Echo Mode | parrot, parrottime |
| RX Toneburst | rxburstfreq, rxburstthreshold, rxbursttime |
| Long Tone Zero (LiTZ) | litzchar, litzcmd, litztime |
| APRS & GPS | aprstt, beaconing |
| Archiving & Logging | archivedir, archiveformat, archivedatefmt, statpost settings |
| Tail Messages | tailmessagelist, tailmessagetime, tailsquashedtime |
| VOTER / RTCM | votermode, votertype, votermargin |
| Advanced | elke, iobase |

### Toggle On/Off
Each setting has a toggle switch. When **off**, the setting is commented out with `;` — preserved but inactive. Toggle back on to re-enable.

### DTMF Stanzas (Functions / Link Functions / Phone Functions)
Table editors for all three function stanzas. Add or remove rows, edit DTMF key and command values. Save each stanza independently. Use the **COP Commands** reference page as a guide.

### Macros
Edit the `[macro]` stanza with a table editor. Format: macro number = colon-separated DTMF command sequence.

### Scheduler
Edit the `[schedule]` stanza. Uses cron-style syntax: `macroN = minute hour dom month dow`. Use `*` for any.

### Telemetry Stanza
Edit courtesy tone definitions. Format: `name = |t(freq,freq2,ms,amplitude)...`

### Nodes List
Edit static node entries in the `[nodes]` stanza.

### COP Commands Reference
Full table of all COP, iLink, and Status commands — useful when building your [functions] stanza.

### Saving
Click **Save Changes** in the yellow bar at the bottom of the screen. A confirmation dialog appears before writing. A timestamped backup is always created automatically.

### Restarting Asterisk
Both **Reload Module** and **Restart Asterisk** buttons show a confirmation dialog before executing.

- **Reload Module** — `asterisk -rx "module reload app_rpt.so"` — softer, faster
- **Restart Asterisk** — `systemctl restart asterisk` — full restart, briefly drops all connections

### Raw Editor
Edit `rpt.conf` as plain text. Useful for stanzas not in the GUI. Confirmation dialog before saving.

---

## Configuration

Environment variables in the systemd service file:

| Variable | Default | Description |
|---|---|---|
| `RPT_CONF_PATH` | `/etc/asterisk/rpt.conf` | Path to rpt.conf |
| `BACKUP_DIR` | `/etc/asterisk/rpt_backups` | Backup directory |
| `RESTART_CMD` | `systemctl restart asterisk` | Asterisk restart command |
| `PORT` | `5000` | Web server port |
| `HOST` | `0.0.0.0` | Bind address |

To change settings:
```bash
sudo nano /etc/systemd/system/ASL3-EZ.service
sudo systemctl daemon-reload
sudo systemctl restart ASL3-EZ
```

---

## Security Notes

⚠️ **This app runs as root** — required to write `/etc/asterisk/rpt.conf` and restart Asterisk.

Recommendations:
1. **Restrict to your LAN** — the firewall rule added by the installer allows all IPs on port 5000. Tighten it if needed.
2. **Do not expose to the internet** without adding authentication.
3. **Change the SECRET_KEY** in the service file.

---

## Uninstall

```bash
sudo bash /opt/ASL3-EZ/uninstall.sh
```

Your `rpt.conf` and backups in `/etc/asterisk/` are **not** deleted.

---

## Troubleshooting

**Service won't start:**
```bash
journalctl -u ASL3-EZ -n 50
```

**Page won't load from another PC:**
```bash
# Open the firewall port manually if the installer didn't do it:
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --reload
# or for ufw:
sudo ufw allow 5000/tcp
```

**"externally managed environment" pip error:**
The installer now handles this automatically by using a virtual environment with `python3-full`. If running manually:
```bash
sudo apt install python3-venv python3-full -y
python3 -m venv venv
venv/bin/pip install flask gunicorn
```

**Segfault / empty reply from server:**
The installer now uses Gunicorn instead of Flask's built-in development server, which resolves segfaults seen on Raspberry Pi.

**Permission denied saving rpt.conf:**
Ensure the service runs as root: check `User=root` in `/etc/systemd/system/ASL3-EZ.service`.

---

## Project Structure

```
ASL3-EZ/
├── app.py                  # Flask backend
├── templates/
│   └── index.html          # Single-page web UI
├── requirements.txt        # Python dependencies
├── ASL3-EZ.service         # systemd service unit file
├── install.sh              # Automated installer
├── uninstall.sh            # Uninstaller
├── sample-rpt.conf         # Sample config for testing
└── README.md               # This file
```

---

## Contact

**N8GMZ**
- 📧 [cq.n8gmz@gmail.com](mailto:cq.n8gmz@gmail.com)
- 🌐 [www.we8chz.org](http://www.we8chz.org)
- 💻 [github.com/GooseThings/ASL3-EZ](https://github.com/GooseThings/ASL3-EZ)

---

## License

GNU GPL v2 — Not affiliated with AllStarLink, Inc. Use at your own risk.

*73 de N8GMZ — happy linking!* 📻
