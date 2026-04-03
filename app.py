#!/usr/bin/env python3
"""
ASL3-EZ - AllStarLink 3 rpt.conf Editor + Node Control
by N8GMZ

FIXES in this version:
  - AMI: proper TCP socket connect with full banner drain before login
  - AMI: correct \r\n\r\n packet terminator (was missing in original)
  - AMI: login response properly validated; detailed error messages logged
  - AMI: response reader handles multi-packet event floods after login
  - rpt.conf save: writes via temp file + atomic rename so partial writes can't corrupt
  - rpt.conf save: explicit flush+fsync before rename
  - Asterisk restart: uses full path /bin/systemctl to avoid PATH issues under gunicorn
  - Asterisk reload: uses /usr/sbin/asterisk full path
  - Node control: corrected ilink command syntax for ASL3 / app_rpt
  - Removed duplicate service name (was referencing both asl3-rpt-editor and ASL3-EZ)
  - No emojis anywhere in output or logs
  - Verbose logging throughout for dashboard debug display
"""

import os
import re
import subprocess
import shutil
import socket
import time
import json
import sqlite3
import threading
import tempfile
import sys
from datetime import datetime
from flask import Flask, render_template, request, jsonify

try:
    import urllib.request as urlreq
except ImportError:
    import urllib2 as urlreq

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration  (all overridable via environment variables in service file)
# ---------------------------------------------------------------------------
RPT_CONF_PATH   = os.environ.get("RPT_CONF_PATH",   "/etc/asterisk/rpt.conf")
MANAGER_CONF    = os.environ.get("MANAGER_CONF",    "/etc/asterisk/manager.conf")
BACKUP_DIR      = os.environ.get("BACKUP_DIR",      "/etc/asterisk/rpt_backups")
SECRET_KEY      = os.environ.get("SECRET_KEY",      "asl3-ez-change-me")
PORT            = int(os.environ.get("PORT",         5000))
HOST            = os.environ.get("HOST",             "0.0.0.0")
DB_PATH         = os.environ.get("DB_PATH",          "/etc/asterisk/asl3ez.db")
AMI_HOST        = os.environ.get("AMI_HOST",         "127.0.0.1")
AMI_PORT        = int(os.environ.get("AMI_PORT",     5038))

# Full paths — do NOT rely on PATH env under gunicorn/systemd
SYSTEMCTL_PATH  = "/bin/systemctl"
if not os.path.exists(SYSTEMCTL_PATH):
    SYSTEMCTL_PATH = "/usr/bin/systemctl"
ASTERISK_PATH   = "/usr/sbin/asterisk"

ASTDB_PATHS = [
    "/var/lib/asterisk/astdb.txt",
    "/var/log/asterisk/astdb.txt",
]
ASL_STATS_URL = "https://stats.allstarlink.org/api/stats/{}"

app.secret_key = SECRET_KEY

# ---------------------------------------------------------------------------
# Logging  (verbose, timestamp-prefixed, written to stdout for journald)
# ---------------------------------------------------------------------------
_log_lock = threading.Lock()

def log(level, msg):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = f"[{ts}] [{level}] {msg}"
    with _log_lock:
        print(out, flush=True)

log("INFO", "ASL3-EZ starting up")
log("INFO", f"  RPT_CONF_PATH  = {RPT_CONF_PATH}")
log("INFO", f"  MANAGER_CONF   = {MANAGER_CONF}")
log("INFO", f"  BACKUP_DIR     = {BACKUP_DIR}")
log("INFO", f"  AMI_HOST:PORT  = {AMI_HOST}:{AMI_PORT}")
log("INFO", f"  DB_PATH        = {DB_PATH}")
log("INFO", f"  Running as UID = {os.getuid()} ({'root' if os.getuid()==0 else 'non-root'})")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        node  TEXT    UNIQUE NOT NULL,
        label TEXT    DEFAULT '',
        added TEXT    DEFAULT (datetime('now'))
    )""")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# AMI credential resolution
# ---------------------------------------------------------------------------
def parse_manager_conf():
    """
    Return dict with keys: user, secret, host, port.

    Priority:
      1. AMI_USER + AMI_SECRET environment variables  (set in service file)
      2. Parse /etc/asterisk/manager.conf directly

    The original code had a subtle bug: it required both 'write' containing
    'command' AND 'enabled = yes' to be explicitly present.  In many ASL3
    default manager.conf files the 'enabled' line is absent (defaults true).
    Fixed: assume enabled=True unless explicitly set to 'no'.
    """
    result = {"user": None, "secret": None, "host": AMI_HOST, "port": AMI_PORT}

    env_user   = os.environ.get("AMI_USER",   "").strip()
    env_secret = os.environ.get("AMI_SECRET", "").strip()
    if env_user and env_secret:
        log("INFO", f"[AMI-CREDS] Using env vars: user='{env_user}'")
        result["user"]   = env_user
        result["secret"] = env_secret
        return result

    log("INFO", f"[AMI-CREDS] AMI_USER/AMI_SECRET not set, parsing {MANAGER_CONF}")
    try:
        with open(MANAGER_CONF) as f:
            raw = f.read()
    except FileNotFoundError:
        log("ERROR", f"[AMI-CREDS] {MANAGER_CONF} not found")
        return result
    except PermissionError:
        log("ERROR", f"[AMI-CREDS] Permission denied reading {MANAGER_CONF}")
        return result
    except Exception as e:
        log("ERROR", f"[AMI-CREDS] Error reading manager.conf: {e}")
        return result

    # Grab port from [general]
    m = re.search(r'^\s*port\s*=\s*(\d+)', raw, re.MULTILINE)
    if m:
        result["port"] = int(m.group(1))
        log("INFO", f"[AMI-CREDS] manager.conf port = {result['port']}")

    # Split into stanzas
    sections = re.split(r'(?m)^(?=\[)', raw)
    for sec in sections:
        lines = sec.strip().splitlines()
        if not lines:
            continue
        hdr_m = re.match(r'^\[([^\]]+)\]', lines[0])
        if not hdr_m:
            continue
        header = hdr_m.group(1).strip()
        if header.lower() == "general":
            continue

        secret    = None
        enabled   = True   # default: enabled unless explicitly 'no'
        can_write = False

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith(";") or not stripped:
                continue
            key_part = stripped.split("=", 1)[0].strip().lower()
            val_part = stripped.split("=", 1)[1].split(";")[0].strip() if "=" in stripped else ""

            if key_part == "secret":
                secret = val_part
            elif key_part == "enabled":
                enabled = val_part.lower() not in ("no", "false", "0")
            elif key_part == "write":
                # accept: all, system, command (any of these grant command exec)
                write_vals = [v.strip().lower() for v in val_part.split(",")]
                if any(v in ("all", "system", "command") for v in write_vals):
                    can_write = True

        log("DEBUG", f"[AMI-CREDS] Section [{header}]: secret={'(set)' if secret else 'MISSING'}, "
                      f"enabled={enabled}, can_write={can_write}")

        if secret and enabled and can_write:
            result["user"]   = header
            result["secret"] = secret
            log("INFO", f"[AMI-CREDS] Selected AMI user: '{header}'")
            return result

    log("ERROR", "[AMI-CREDS] No valid AMI user found in manager.conf. "
                 "Set AMI_USER and AMI_SECRET in the service file.")
    return result


# ---------------------------------------------------------------------------
# AMI TCP client  (raw socket, matching AllScan's proven approach)
# ---------------------------------------------------------------------------
class AMIClient:
    """
    Low-level Asterisk Manager Interface client over a raw TCP socket.

    Key fixes vs original:
      - Banner is fully drained before sending Login (Asterisk sends the
        banner as a standalone packet ending with \r\n\r\n; not doing this
        causes the first read_packet() call to mix banner + login response).
      - Packet delimiter is always \r\n\r\n (two CRLF pairs).
      - After login Asterisk may flood queued events; we drain them until
        we see the actual Login Response packet.
      - Command output is read until '--END COMMAND--' sentinel OR timeout.
      - All steps are logged at DEBUG level for dashboard visibility.
    """

    def __init__(self, host, port, user, secret, timeout=10):
        self.host    = host
        self.port    = port
        self.user    = user
        self.secret  = secret
        self.timeout = timeout
        self._sock   = None
        self._buf    = ""

    # ------------------------------------------------------------------ I/O
    def _raw_send(self, data: str):
        self._sock.sendall(data.encode("utf-8"))

    def _raw_recv(self) -> str:
        try:
            data = self._sock.recv(4096)
            if not data:
                return ""
            return data.decode("utf-8", errors="replace")
        except socket.timeout:
            return ""

    def _read_packet(self) -> dict:
        """
        Read from the socket until we have a complete AMI packet
        (terminated by \\r\\n\\r\\n), then parse into a dict.
        """
        deadline = time.time() + self.timeout
        while "\r\n\r\n" not in self._buf:
            if time.time() > deadline:
                log("WARN", "[AMI] Timeout waiting for packet delimiter")
                break
            chunk = self._raw_recv()
            if chunk:
                self._buf += chunk

        if "\r\n\r\n" in self._buf:
            packet_raw, self._buf = self._buf.split("\r\n\r\n", 1)
        else:
            packet_raw = self._buf
            self._buf  = ""

        result = {}
        for line in packet_raw.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
        return result

    def _send_action(self, params: dict):
        msg = "".join(f"{k}: {v}\r\n" for k, v in params.items()) + "\r\n"
        log("DEBUG", f"[AMI] >> Action: {params.get('Action', '?')}")
        self._raw_send(msg)

    # --------------------------------------------------------------- connect
    def connect(self):
        log("INFO", f"[AMI] Connecting to {self.host}:{self.port}")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        try:
            self._sock.connect((self.host, self.port))
        except ConnectionRefusedError:
            raise Exception(
                f"AMI connection refused at {self.host}:{self.port}. "
                "Is Asterisk running? Is manager.conf 'enabled = yes'?"
            )
        except socket.timeout:
            raise Exception(
                f"AMI connection timed out to {self.host}:{self.port}"
            )

        # Drain the banner line (e.g. "Asterisk Call Manager/6.0.0\r\n")
        # The banner is NOT a key:value packet — read it as raw text.
        deadline = time.time() + 5
        banner   = ""
        while "\r\n" not in banner:
            if time.time() > deadline:
                break
            chunk = self._raw_recv()
            if chunk:
                banner += chunk
        banner = banner.strip()
        log("INFO", f"[AMI] Banner received: {banner!r}")

        # Send Login
        self._send_action({
            "Action":   "Login",
            "Username": self.user,
            "Secret":   self.secret,
            "Events":   "off",   # suppress event flood during command sessions
        })

        # Read packets until we find the Login Response
        # (Asterisk may send queued events before the response)
        for attempt in range(20):
            pkt = self._read_packet()
            log("DEBUG", f"[AMI] Login read [{attempt}]: {pkt}")
            if pkt.get("Response"):
                if pkt["Response"] == "Success":
                    log("INFO", f"[AMI] Login successful as '{self.user}'")
                    return
                else:
                    msg = pkt.get("Message", "unknown error")
                    raise Exception(
                        f"AMI login failed: {msg} "
                        f"(user='{self.user}', host={self.host}:{self.port}). "
                        "Check AMI_USER/AMI_SECRET in the service file and "
                        "that manager.conf has 'write = system,call,log,verbose,command,agent,user,config,dtmf,reporting,cdr,dialplan'."
                    )
        raise Exception("[AMI] Never received Login Response after 20 packets")

    def close(self):
        try:
            if self._sock:
                self._send_action({"Action": "Logoff"})
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        log("DEBUG", "[AMI] Connection closed")

    # ---------------------------------------------------------- AMI Command
    def command(self, cmd: str) -> list:
        """
        Send an AMI Command action and collect all Output: lines until
        the '--END COMMAND--' sentinel appears.
        Returns a list of output line strings.
        """
        log("INFO", f"[AMI] Command: {cmd!r}")
        self._send_action({"Action": "Command", "Command": cmd})

        output_lines = []
        raw_accum    = ""
        deadline     = time.time() + self.timeout

        while time.time() < deadline:
            chunk = self._raw_recv()
            if chunk:
                raw_accum += chunk
            if "--END COMMAND--" in raw_accum:
                break
            # Also stop on a Response: Error
            if "Response: Error" in raw_accum and "\r\n\r\n" in raw_accum:
                break

        # Consume from buffer so next read starts clean
        if "\r\n\r\n" in raw_accum:
            # There may be multiple packets; consume all
            self._buf = raw_accum.split("\r\n\r\n")[-1]
        
        for line in raw_accum.splitlines():
            line = line.strip()
            if line.startswith("Output:"):
                output_lines.append(line[7:].strip())
            elif line.startswith("Response: Error"):
                log("WARN", f"[AMI] Command error response for: {cmd!r}")

        log("DEBUG", f"[AMI] Command output lines: {len(output_lines)}")
        return output_lines

    # --------------------------------------------------- app_rpt helpers
    def rpt_cmd(self, node: str, subcmd: str) -> list:
        """
        Issue: rpt cmd <node> <subcmd>
        This is the correct AMI command syntax for app_rpt in ASL3.
        """
        return self.command(f"rpt cmd {node} {subcmd}")

    def get_node_status(self, node: str) -> dict:
        """
        Use 'rpt show variables <node>' to get keyed status and linked nodes.
        Falls back to 'rpt lstats <node>' if variables returns nothing useful.
        """
        status = {"keyed": False, "connected": [], "raw": []}

        lines = self.command(f"rpt show variables {node}")
        status["raw"] = lines
        log("DEBUG", f"[AMI] rpt show variables {node}: {lines}")

        for line in lines:
            if "RPT_RXKEYED" in line and "=1" in line:
                status["keyed"] = True
            # Collect linked node numbers
            nums = re.findall(r'\b(\d{4,7})\b', line)
            for n in nums:
                if n != str(node) and n not in status["connected"]:
                    status["connected"].append(n)

        # Also pull lstats for richer connected-node info
        lstats = self.command(f"rpt lstats {node}")
        log("DEBUG", f"[AMI] rpt lstats {node}: {lstats}")
        status["lstats"] = lstats
        for line in lstats:
            nums = re.findall(r'\b(\d{4,7})\b', line)
            for n in nums:
                if n != str(node) and n not in status["connected"]:
                    status["connected"].append(n)

        return status


def ami_session() -> AMIClient:
    cfg = parse_manager_conf()
    if not cfg.get("user") or not cfg.get("secret"):
        raise Exception(
            "AMI credentials not configured. "
            "Set AMI_USER and AMI_SECRET in /etc/systemd/system/ASL3-EZ.service "
            "then run: systemctl daemon-reload && systemctl restart ASL3-EZ"
        )
    client = AMIClient(cfg["host"], cfg["port"], cfg["user"], cfg["secret"])
    client.connect()
    return client


# ---------------------------------------------------------------------------
# rpt.conf file helpers
# ---------------------------------------------------------------------------
def read_conf_file(path):
    try:
        with open(path) as f:
            content = f.read()
        log("DEBUG", f"[CONF] Read {len(content)} bytes from {path}")
        return content
    except FileNotFoundError:
        log("ERROR", f"[CONF] File not found: {path}")
        return None
    except PermissionError:
        log("ERROR", f"[CONF] Permission denied reading {path} (running as UID {os.getuid()})")
        return None
    except Exception as e:
        log("ERROR", f"[CONF] Error reading {path}: {e}")
        return None


def write_conf_file(path, content):
    """
    Atomically write content to path:
      1. Create timestamped backup of existing file
      2. Write to a temp file in the same directory
      3. fsync + rename (atomic on Linux)

    This prevents partial writes from corrupting rpt.conf.
    Raises PermissionError / OSError on failure.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Backup existing file
    backup_path = None
    if os.path.exists(path):
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"rpt.conf.{ts}.bak")
        shutil.copy2(path, backup_path)
        log("INFO", f"[CONF] Backup created: {backup_path}")

    # Write atomically via temp file
    conf_dir = os.path.dirname(path)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=conf_dir, prefix=".rpt_tmp_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp_path, path)
            log("INFO", f"[CONF] Saved {len(content)} bytes to {path}")
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
    except PermissionError as e:
        log("ERROR", f"[CONF] Permission denied writing {path}: {e}. "
                     f"Running as UID {os.getuid()}. Service must run as root (User=root).")
        raise

    return backup_path


def get_node_numbers(content):
    nodes = []
    for line in content.splitlines():
        m = re.match(r'^\s*\[(\d{4,7})\]', line)
        if m:
            nodes.append(m.group(1))
    return nodes


def parse_node_settings(content):
    settings = {}
    for line in content.splitlines():
        stripped  = line.strip()
        commented = stripped.startswith(";")
        if commented:
            stripped = stripped[1:].strip()
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;]*?)(?:\s*;.*)?$', stripped)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            settings[k] = {"value": v, "commented": commented, "raw_line": line}
    return settings


def update_setting_in_content(content, section, key, value, enable=True):
    """Update or insert a key=value in the given section of the config."""
    lines   = content.splitlines(keepends=True)
    result  = []
    in_sec  = False
    found   = False

    for line in lines:
        s = line.strip()
        sec_m = re.match(r'^\[([^\]\(]+)', s)
        if sec_m:
            in_sec = (sec_m.group(1).strip() == section)

        if in_sec and not found:
            test = s.lstrip(";").strip()
            km   = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', test)
            if km and km.group(1) == key:
                found = True
                prefix = "" if enable else ";"
                result.append(f"{prefix}{key} = {value}\n")
                continue

        result.append(line)

    if not found:
        # Insert at end of target section
        new_lines  = []
        in_target  = False
        inserted   = False
        for line in result:
            s     = line.strip()
            sec_m = re.match(r'^\[([^\]\(]+)', s)
            if sec_m:
                if in_target and not inserted:
                    prefix = "" if enable else ";"
                    new_lines.append(f"{prefix}{key} = {value}\n")
                    inserted = True
                in_target = (sec_m.group(1).strip() == section)
            new_lines.append(line)
        if in_target and not inserted:
            prefix = "" if enable else ";"
            new_lines.append(f"{prefix}{key} = {value}\n")
        result = new_lines

    return "".join(result)


# ---------------------------------------------------------------------------
# astdb / node lookup
# ---------------------------------------------------------------------------
_astdb_cache  = {}
_astdb_loaded = False
_astdb_lock   = threading.Lock()


def load_astdb():
    global _astdb_cache, _astdb_loaded
    with _astdb_lock:
        for path in ASTDB_PATHS:
            if os.path.exists(path):
                try:
                    count = 0
                    with open(path) as f:
                        for line in f:
                            parts = [p.strip() for p in line.strip().split(",")]
                            if len(parts) >= 2:
                                node     = parts[0]
                                callsign = parts[1] if len(parts) > 1 else ""
                                desc     = parts[2]  if len(parts) > 2 else ""
                                location = parts[3]  if len(parts) > 3 else ""
                                _astdb_cache[node] = {
                                    "callsign": callsign,
                                    "desc":     desc,
                                    "location": location,
                                }
                                count += 1
                    _astdb_loaded = True
                    log("INFO", f"[ASTDB] Loaded {count} nodes from {path}")
                    return True
                except Exception as e:
                    log("ERROR", f"[ASTDB] Failed to load {path}: {e}")
    log("WARN", "[ASTDB] No astdb.txt found at any expected path")
    return False


def lookup_node(node):
    if not _astdb_loaded:
        load_astdb()
    return _astdb_cache.get(str(node), {"callsign": "", "desc": "", "location": ""})


# ---------------------------------------------------------------------------
# System info helpers
# ---------------------------------------------------------------------------
def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        pass
    try:
        r = subprocess.run(["vcgencmd", "measure_temp"],
                           capture_output=True, text=True, timeout=3)
        m = re.search(r'[\d.]+', r.stdout)
        if m:
            return float(m.group())
    except Exception:
        pass
    return None


def get_disk_usage():
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        lines = r.stdout.splitlines()
        if len(lines) >= 2:
            p = lines[1].split()
            return {"total": p[1], "used": p[2], "avail": p[3], "pct": p[4]}
    except Exception:
        pass
    return {}


def get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        d = int(secs // 86400)
        h = int((secs % 86400) // 3600)
        m = int((secs % 3600) // 60)
        return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"
    except Exception:
        return "unknown"


def get_asl_version():
    try:
        r = subprocess.run(["dpkg", "-l", "asl3"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if line.startswith("ii"):
                return line.split()[2]
    except Exception:
        pass
    return "unknown"


def get_asterisk_status():
    """Return dict with running/not and basic version info."""
    try:
        r = subprocess.run([SYSTEMCTL_PATH, "is-active", "asterisk"],
                           capture_output=True, text=True, timeout=5)
        active = r.stdout.strip() == "active"
    except Exception:
        active = False

    version = "unknown"
    try:
        r = subprocess.run([ASTERISK_PATH, "-rx", "core show version"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            version = r.stdout.strip().splitlines()[0]
    except Exception:
        pass

    return {"active": active, "version": version}


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    content = read_conf_file(RPT_CONF_PATH)
    nodes   = get_node_numbers(content) if content else []
    return render_template("index.html",
                           conf_exists=content is not None,
                           nodes=nodes,
                           conf_path=RPT_CONF_PATH)


# ── rpt.conf API ──────────────────────────────────────────────────────────────

@app.route("/api/conf")
def api_get_conf():
    content = read_conf_file(RPT_CONF_PATH)
    if content is None:
        log("ERROR", f"[API] /api/conf: cannot read {RPT_CONF_PATH}")
        return jsonify({"error": "Cannot read rpt.conf", "path": RPT_CONF_PATH,
                        "hint": "Ensure the service runs as root (User=root in service file)"}), 404
    return jsonify({
        "content":          content,
        "nodes":            get_node_numbers(content),
        "general_settings": parse_node_settings(content),
    })


@app.route("/api/save", methods=["POST"])
def api_save():
    data    = request.json or {}
    content = read_conf_file(RPT_CONF_PATH) or ""
    raw     = data.get("raw_content")

    if raw is not None:
        log("INFO", f"[API] /api/save raw content ({len(raw)} bytes)")
        try:
            backup = write_conf_file(RPT_CONF_PATH, raw)
            return jsonify({"success": True, "backup": backup,
                            "message": f"Saved. Backup: {backup}"})
        except PermissionError as e:
            return jsonify({"error": str(e),
                            "hint": "Service must run as root. Check User=root in ASL3-EZ.service"}), 403
        except Exception as e:
            log("ERROR", f"[API] /api/save error: {e}")
            return jsonify({"error": str(e)}), 500

    section = data.get("section", "")
    changes = data.get("changes", {})
    log("INFO", f"[API] /api/save section={section!r} changes={list(changes.keys())}")

    for key, info in changes.items():
        content = update_setting_in_content(
            content, section, key,
            info.get("value", ""), enable=info.get("enabled", True)
        )

    try:
        backup = write_conf_file(RPT_CONF_PATH, content)
        return jsonify({"success": True, "backup": backup,
                        "message": f"Saved {len(changes)} setting(s). Backup: {backup}"})
    except PermissionError as e:
        return jsonify({"error": str(e),
                        "hint": "Service must run as root. Check User=root in ASL3-EZ.service"}), 403
    except Exception as e:
        log("ERROR", f"[API] /api/save error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Full Asterisk restart via systemctl."""
    log("INFO", "[API] /api/restart called")
    try:
        r = subprocess.run(
            [SYSTEMCTL_PATH, "restart", "asterisk"],
            capture_output=True, text=True, timeout=30
        )
        log("INFO", f"[API] systemctl restart asterisk -> rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
        if r.returncode == 0:
            return jsonify({"success": True,
                            "output": r.stdout or "Asterisk restarted successfully.",
                            "command": f"{SYSTEMCTL_PATH} restart asterisk"})
        return jsonify({
            "error":     r.stderr.strip() or f"systemctl returned code {r.returncode}",
            "stdout":    r.stdout,
            "returncode": r.returncode,
            "hint":      "Check: systemctl status asterisk  and  journalctl -u asterisk -n 30",
        }), 500
    except PermissionError as e:
        return jsonify({"error": str(e),
                        "hint": "Service must run as root to call systemctl restart"}), 403
    except Exception as e:
        log("ERROR", f"[API] /api/restart exception: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reload", methods=["POST"])
def api_reload():
    """Soft app_rpt module reload."""
    log("INFO", "[API] /api/reload called")
    try:
        r = subprocess.run(
            [ASTERISK_PATH, "-rx", "module reload app_rpt.so"],
            capture_output=True, text=True, timeout=15
        )
        log("INFO", f"[API] asterisk -rx module reload -> rc={r.returncode} out={r.stdout!r}")
        return jsonify({"success": True,
                        "output":  r.stdout.strip() or "Module reload sent.",
                        "command": f"{ASTERISK_PATH} -rx 'module reload app_rpt.so'"})
    except Exception as e:
        log("ERROR", f"[API] /api/reload exception: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/backups")
def api_backups():
    if not os.path.exists(BACKUP_DIR):
        return jsonify({"backups": [], "backup_dir": BACKUP_DIR})
    files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith(".bak")],
        reverse=True
    )[:10]
    return jsonify({"backups": files, "backup_dir": BACKUP_DIR})


@app.route("/api/backup/<filename>")
def api_get_backup(filename):
    if not re.match(r'^rpt\.conf\.\d{8}_\d{6}\.bak$', filename):
        return jsonify({"error": "Invalid filename"}), 400
    path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404
    with open(path) as f:
        return jsonify({"content": f.read(), "filename": filename})


# ── Favorites API ─────────────────────────────────────────────────────────────

@app.route("/api/favorites")
def api_favorites():
    try:
        db   = get_db()
        rows = db.execute("SELECT * FROM favorites ORDER BY id").fetchall()
        favs = [dict(r) for r in rows]
        for fav in favs:
            fav.update(lookup_node(fav["node"]))
        return jsonify({"favorites": favs})
    except Exception as e:
        log("ERROR", f"[API] /api/favorites: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/favorites/add", methods=["POST"])
def api_fav_add():
    data  = request.json or {}
    node  = str(data.get("node",  "")).strip()
    label = str(data.get("label", "")).strip()
    if not node or not node.isdigit():
        return jsonify({"error": "Invalid node number"}), 400
    if not label:
        info  = lookup_node(node)
        label = info.get("callsign") or info.get("desc") or f"Node {node}"
    try:
        db = get_db()
        db.execute("INSERT OR IGNORE INTO favorites (node, label) VALUES (?,?)", (node, label))
        db.commit()
        log("INFO", f"[API] Favorite added: node={node} label={label!r}")
        return jsonify({"success": True, "node": node, "label": label})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/favorites/delete", methods=["POST"])
def api_fav_delete():
    data = request.json or {}
    node = str(data.get("node", "")).strip()
    try:
        db = get_db()
        db.execute("DELETE FROM favorites WHERE node=?", (node,))
        db.commit()
        log("INFO", f"[API] Favorite deleted: node={node}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/favorites/label", methods=["POST"])
def api_fav_label():
    data  = request.json or {}
    node  = str(data.get("node",  "")).strip()
    label = str(data.get("label", "")).strip()
    try:
        db = get_db()
        db.execute("UPDATE favorites SET label=? WHERE node=?", (label, node))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── AllStarLink stats proxy (avoids CORS from browser) ───────────────────────

@app.route("/api/nodestats/<node>")
def api_node_stats(node):
    if not node.isdigit():
        return jsonify({"error": "Invalid node"}), 400
    try:
        url = ASL_STATS_URL.format(node)
        log("DEBUG", f"[API] Fetching stats for node {node} from {url}")
        req = urlreq.Request(url, headers={"User-Agent": "ASL3-EZ/1.0"})
        with urlreq.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return jsonify(data)
    except Exception as e:
        log("WARN", f"[API] nodestats {node}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/nodestats/batch", methods=["POST"])
def api_nodestats_batch():
    data  = request.json or {}
    nodes = data.get("nodes", [])
    results = {}
    log("INFO", f"[API] nodestats/batch for {len(nodes)} nodes")
    for node in nodes[:15]:
        try:
            url = ASL_STATS_URL.format(node)
            req = urlreq.Request(url, headers={"User-Agent": "ASL3-EZ/1.0"})
            with urlreq.urlopen(req, timeout=6) as resp:
                results[node] = json.loads(resp.read().decode())
        except Exception as e:
            results[node] = {"error": str(e)}
        time.sleep(0.15)
    return jsonify(results)


# ── AMI node control API ──────────────────────────────────────────────────────

@app.route("/api/ami/status")
def api_ami_status():
    content = read_conf_file(RPT_CONF_PATH)
    nodes   = get_node_numbers(content) if content else []
    if not nodes:
        return jsonify({"error": "No nodes found in rpt.conf"}), 404
    node = request.args.get("node", nodes[0])
    log("INFO", f"[API] /api/ami/status node={node}")
    try:
        ami    = ami_session()
        status = ami.get_node_status(node)
        ami.close()
        return jsonify({"node": node, **status})
    except Exception as e:
        log("ERROR", f"[API] /api/ami/status error: {e}")
        return jsonify({"error": str(e), "node": node,
                        "hint": "Verify AMI_USER and AMI_SECRET in the service file"}), 500


@app.route("/api/ami/connect", methods=["POST"])
def api_ami_connect():
    data        = request.json or {}
    local_node  = str(data.get("local_node",  "")).strip()
    remote_node = str(data.get("remote_node", "")).strip()
    mode        = str(data.get("mode", "3"))      # 3=transceive, 2=monitor
    disc_first  = data.get("disconnect_first", False)

    if not local_node or not remote_node:
        return jsonify({"error": "local_node and remote_node required"}), 400
    if not local_node.isdigit() or not remote_node.isdigit():
        return jsonify({"error": "Node numbers must be numeric"}), 400

    log("INFO", f"[API] /api/ami/connect local={local_node} remote={remote_node} mode={mode} disc_first={disc_first}")
    try:
        ami    = ami_session()
        output = []
        if disc_first:
            log("INFO", f"[API] Disconnecting all first on node {local_node}")
            out = ami.rpt_cmd(local_node, "ilink 6")
            output.extend(out)
            time.sleep(0.6)
        out = ami.rpt_cmd(local_node, f"ilink {mode} {remote_node}")
        output.extend(out)
        ami.close()
        log("INFO", f"[API] Connect result: {output}")
        return jsonify({"success": True, "output": output,
                        "command": f"rpt cmd {local_node} ilink {mode} {remote_node}"})
    except Exception as e:
        log("ERROR", f"[API] /api/ami/connect error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ami/disconnect", methods=["POST"])
def api_ami_disconnect():
    data        = request.json or {}
    local_node  = str(data.get("local_node",  "")).strip()
    remote_node = str(data.get("remote_node", "")).strip()

    if not local_node:
        return jsonify({"error": "local_node required"}), 400

    log("INFO", f"[API] /api/ami/disconnect local={local_node} remote={remote_node or '(all)'}")
    try:
        ami = ami_session()
        if remote_node:
            out = ami.rpt_cmd(local_node, f"ilink 1 {remote_node}")
        else:
            out = ami.rpt_cmd(local_node, "ilink 6")
        ami.close()
        log("INFO", f"[API] Disconnect result: {out}")
        return jsonify({"success": True, "output": out})
    except Exception as e:
        log("ERROR", f"[API] /api/ami/disconnect error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ami/perm_connect", methods=["POST"])
def api_ami_perm_connect():
    """Permanent connection (survives Asterisk reload)."""
    data        = request.json or {}
    local_node  = str(data.get("local_node",  "")).strip()
    remote_node = str(data.get("remote_node", "")).strip()
    mode        = str(data.get("mode", "13"))  # 13=perm transceive, 12=perm monitor

    if not local_node or not remote_node:
        return jsonify({"error": "local_node and remote_node required"}), 400

    log("INFO", f"[API] /api/ami/perm_connect local={local_node} remote={remote_node} mode={mode}")
    try:
        ami = ami_session()
        out = ami.rpt_cmd(local_node, f"ilink {mode} {remote_node}")
        ami.close()
        return jsonify({"success": True, "output": out,
                        "command": f"rpt cmd {local_node} ilink {mode} {remote_node}"})
    except Exception as e:
        log("ERROR", f"[API] /api/ami/perm_connect error: {e}")
        return jsonify({"error": str(e)}), 500


# ── System info API ───────────────────────────────────────────────────────────

@app.route("/api/sysinfo")
def api_sysinfo():
    creds       = parse_manager_conf()
    ami_user    = creds.get("user") or "NOT CONFIGURED"
    ast_status  = get_asterisk_status()
    return jsonify({
        "cpu_temp":        get_cpu_temp(),
        "disk":            get_disk_usage(),
        "uptime":          get_uptime(),
        "asl_version":     get_asl_version(),
        "asterisk_active": ast_status["active"],
        "asterisk_version": ast_status["version"],
        "ami_user":        ami_user,
        "ami_host":        f"{AMI_HOST}:{AMI_PORT}",
        "running_as":      "root" if os.getuid() == 0 else f"uid={os.getuid()} (NOT ROOT - some features will fail)",
        "rpt_conf_path":   RPT_CONF_PATH,
        "rpt_conf_exists": os.path.exists(RPT_CONF_PATH),
        "rpt_conf_writable": os.access(RPT_CONF_PATH, os.W_OK),
    })


@app.route("/api/lookup/<node>")
def api_lookup(node):
    if not node.isdigit():
        return jsonify({"error": "Invalid node"}), 400
    info = lookup_node(node)
    return jsonify({"node": node, **info})


# ── AMI connectivity test ─────────────────────────────────────────────────────

@app.route("/api/ami/test")
def api_ami_test():
    """Quick AMI connectivity and auth test — useful for dashboard diagnostics."""
    log("INFO", "[API] /api/ami/test")
    creds = parse_manager_conf()
    result = {
        "ami_host":   f"{creds.get('host')}:{creds.get('port')}",
        "ami_user":   creds.get("user") or "NOT CONFIGURED",
        "creds_found": bool(creds.get("user") and creds.get("secret")),
        "connected":  False,
        "error":      None,
    }
    if not result["creds_found"]:
        result["error"] = ("AMI credentials not found. Set AMI_USER and AMI_SECRET "
                           "in /etc/systemd/system/ASL3-EZ.service")
        return jsonify(result), 500
    try:
        ami = ami_session()
        # Run a benign command to confirm we can issue commands
        out = ami.command("core show version")
        ami.close()
        result["connected"]      = True
        result["asterisk_info"]  = out[0] if out else "connected"
    except Exception as e:
        result["error"] = str(e)
        return jsonify(result), 500
    return jsonify(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log("INFO", "Starting in direct-run mode (not via gunicorn)")
    load_astdb()
    app.run(host=HOST, port=PORT, debug=False)
