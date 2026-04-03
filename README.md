!!! Warning - There are known issues with rpt.conf editing and saving where it will nuke your rpt.conf file. !!!

!!! Use at your own risk. !!!

# ASL3-EZ - AllStarLink 3 Node Manager

A browser-based web interface for managing your AllStarLink 3 nodes:
- Edit `rpt.conf` with field-by-field or raw text editing
- Connect, disconnect, and monitor nodes via AMI (Asterisk Manager Interface)
- Automatic backups on every save
- Dashboard with system status and verbose debug logging
- Node lookup from local astdb and AllStarLink stats API
- Restart Asterisk from the Dashboard

---

## Requirements

- AllStarLink 3 on Debian 12 (Bookworm) or 13 (Trixie)
- Python 3.8 or later
- Root access (required to write `/etc/asterisk/rpt.conf` and restart Asterisk)

---

## Quick Install

```bash
git clone https://github.com/GooseThings/ASL3-EZ.git
cd ASL3-EZ
sudo bash install.sh
```

Then open: `http://YOUR_NODE_IP:5000`

---

## Manual AMI Setup (Required for Node Control)
!!! Do not do this if automatic setup worked !!!
Check in AMI Diagnostics in the Dashboard

The Node Control and status features require AMI credentials.

### Step 1 — Configure manager.conf

Edit `/etc/asterisk/manager.conf`:

```ini
[general]
enabled = yes
port = 5038
bindaddr = 127.0.0.1

[admin]
secret = your_secret_here
read = system,call,log,verbose,command,agent,user,config,dtmf,reporting,cdr,dialplan
write = system,call,log,verbose,command,agent,user,config,dtmf,reporting,cdr,dialplan
permit = 127.0.0.1/255.255.255.0
```

Reload Asterisk after editing:
```bash
sudo asterisk -rx "module reload manager"
```

### Step 2 — Set credentials in service file

```bash
sudo nano /etc/systemd/system/ASL3-EZ.service
```

Set these two lines to match your manager.conf:
```
Environment="AMI_USER=admin"
Environment="AMI_SECRET=your_secret_here"
```

Then apply:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ASL3-EZ
```

### Step 3 — Verify

Go to **AMI Diagnostics** in the web UI and click **Run Test**. You should see a green success message.

---

## Troubleshooting

**Service won't start:**
```bash
journalctl -u ASL3-EZ -n 50
systemctl status ASL3-EZ
```

**Permission denied saving rpt.conf:**
- The service must run as root. Verify `User=root` is in the service file.
- Check: `ls -la /etc/asterisk/rpt.conf`

**AMI login failed:**
- Check `AMI_USER` and `AMI_SECRET` in the service file match exactly what is in manager.conf.
- Verify `enabled = yes` in `[general]` of manager.conf.
- Verify the user stanza has `write` including `command`.
- Check Asterisk is running: `systemctl status asterisk`

**Asterisk restart fails from the UI:**
- Service must run as root.
- Verify Asterisk is managed by systemd: `systemctl status asterisk`

**Node Control not connecting:**
- Confirm AMI test passes first (AMI Diagnostics page).
- Verify your node number appears in the local node dropdown.
- Check the rpt.conf has a valid `[NODENUMBER]` stanza.

---

## Environment Variables

All settings can be overridden in the service file:

| Variable        | Default                       | Description                          |
|-----------------|-------------------------------|--------------------------------------|
| `AMI_USER`      | (none)                        | AMI username — MUST be set           |
| `AMI_SECRET`    | (none)                        | AMI password — MUST be set           |
| `AMI_HOST`      | `127.0.0.1`                   | Asterisk host                        |
| `AMI_PORT`      | `5038`                        | AMI TCP port                         |
| `RPT_CONF_PATH` | `/etc/asterisk/rpt.conf`      | Path to rpt.conf                     |
| `MANAGER_CONF`  | `/etc/asterisk/manager.conf`  | Path to manager.conf                 |
| `BACKUP_DIR`    | `/etc/asterisk/rpt_backups`   | Backup directory                     |
| `PORT`          | `5000`                        | Web server port                      |
| `HOST`          | `0.0.0.0`                     | Bind address                         |
| `SECRET_KEY`    | `asl3-ez-change-me`           | Flask session key (change this!)     |

---

## File Structure

```
ASL3-EZ/
├── app.py                  # Flask backend
├── templates/
│   └── index.html          # Single-page web UI
├── requirements.txt        # Python deps (flask, gunicorn)
├── ASL3-EZ.service         # systemd unit file
├── install.sh              # Installer
├── uninstall.sh            # Uninstaller
├── sample-rpt.conf         # Sample for testing
└── README.md
```

---

## License

GPL-2.0 — use freely, at your own risk. Not affiliated with AllStarLink, Inc.

*73 de N8GMZ*
