#!/usr/bin/env python3
"""
ASL3-EZ - AllStarLink 3 rpt.conf Editor + Node Control
by N8GMZ
"""

import os, re, subprocess, shutil, socket, time, json, sqlite3, threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response
try:
    import urllib.request as urlreq
except ImportError:
    import urllib2 as urlreq

app = Flask(__name__)

# --- Configuration ---
RPT_CONF_PATH  = os.environ.get("RPT_CONF_PATH",  "/etc/asterisk/rpt.conf")
MANAGER_CONF   = os.environ.get("MANAGER_CONF",   "/etc/asterisk/manager.conf")
BACKUP_DIR     = os.environ.get("BACKUP_DIR",     "/etc/asterisk/rpt_backups")
RESTART_CMD    = os.environ.get("RESTART_CMD",    "systemctl restart asterisk")
SECRET_KEY     = os.environ.get("SECRET_KEY",     "asl3-ez-change-me")
PORT           = int(os.environ.get("PORT",       5000))
HOST           = os.environ.get("HOST",           "0.0.0.0")
DB_PATH        = os.environ.get("DB_PATH",        "/etc/asterisk/asl3ez.db")
AMI_HOST       = os.environ.get("AMI_HOST",       "127.0.0.1")
AMI_PORT       = int(os.environ.get("AMI_PORT",   5038))
ASTDB_PATHS    = [
    "/var/lib/asterisk/astdb.txt",
    "/var/log/asterisk/astdb.txt",
]
ASL_STATS_URL  = "https://stats.allstarlink.org/api/stats/{}"

app.secret_key = SECRET_KEY

# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        node    TEXT    UNIQUE NOT NULL,
        label   TEXT    DEFAULT '',
        added   TEXT    DEFAULT (datetime('now'))
    )""")
    conn.commit()
    return conn

# ─── manager.conf reader ─────────────────────────────────────────────────────

def parse_manager_conf():
    """Return dict {user, secret, port} from manager.conf."""
    result = {"user": "admin", "secret": "", "host": AMI_HOST, "port": AMI_PORT}
    try:
        with open(MANAGER_CONF) as f:
            content = f.read()
        # Find enabled port
        m = re.search(r'^\s*port\s*=\s*(\d+)', content, re.MULTILINE)
        if m:
            result["port"] = int(m.group(1))
        # Find first [user] stanza that isn't [general]
        sections = re.split(r'^\[', content, flags=re.MULTILINE)
        for sec in sections:
            lines = sec.strip().splitlines()
            if not lines:
                continue
            header = lines[0].rstrip(']').strip()
            if header.lower() in ('general', ''):
                continue
            result["user"] = header
            for line in lines[1:]:
                m2 = re.match(r'^\s*secret\s*=\s*(.+)', line)
                if m2:
                    result["secret"] = m2.group(1).strip()
                    break
            break
    except Exception as e:
        pass
    return result

# ─── AMI client ──────────────────────────────────────────────────────────────

class AMIClient:
    def __init__(self, host, port, user, secret, timeout=10):
        self.host    = host
        self.port    = port
        self.user    = user
        self.secret  = secret
        self.timeout = timeout
        self._sock   = None
        self._buf    = ""

    def connect(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        self._read_response()   # banner
        self._action({"Action": "Login", "Username": self.user, "Secret": self.secret})
        resp = self._read_response()
        if "Success" not in resp.get("Response", ""):
            raise Exception("AMI login failed: " + resp.get("Message", "unknown"))

    def close(self):
        try:
            if self._sock:
                self._action({"Action": "Logoff"})
                self._sock.close()
        except:
            pass
        self._sock = None

    def _send(self, text):
        self._sock.sendall(text.encode("utf-8"))

    def _recv_chunk(self):
        try:
            data = self._sock.recv(4096)
            return data.decode("utf-8", errors="replace")
        except socket.timeout:
            return ""

    def _read_response(self):
        """Read until blank line, return dict of key:value pairs."""
        while "\r\n\r\n" not in self._buf:
            chunk = self._recv_chunk()
            if not chunk:
                break
            self._buf += chunk
        parts = self._buf.split("\r\n\r\n", 1)
        block = parts[0]
        self._buf = parts[1] if len(parts) > 1 else ""
        result = {}
        for line in block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
        return result

    def _action(self, params):
        msg = "".join(f"{k}: {v}\r\n" for k, v in params.items()) + "\r\n"
        self._send(msg)

    def command(self, cmd):
        """Send an AMI Command action, return output lines."""
        self._action({"Action": "Command", "Command": cmd})
        # Read until we get "--END COMMAND--"
        output = ""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            chunk = self._recv_chunk()
            output += chunk
            if "--END COMMAND--" in output or "Error" in output:
                break
        lines = output.splitlines()
        result = []
        for line in lines:
            if line.startswith("Output:"):
                result.append(line[7:].strip())
        return result

    def rpt_cmd(self, node, cmd):
        """Send rpt cmd <node> <cmd> via AMI Command."""
        return self.command(f"rpt cmd {node} {cmd}")

    def get_connected_nodes(self, node):
        """Return list of currently connected node numbers."""
        lines = self.command(f"rpt show channels {node}")
        connected = []
        for line in lines:
            m = re.search(r'(\d{4,7})', line)
            if m:
                n = m.group(1)
                if n != str(node):
                    connected.append(n)
        return connected

    def get_node_status(self, node):
        """Return dict with keyed status and connected nodes."""
        lines = self.command(f"rpt show variables {node}")
        status = {"keyed": False, "connected": [], "raw": lines}
        for line in lines:
            if "RPT_RXKEYED" in line and "=1" in line:
                status["keyed"] = True
            if "RPT_LINKS" in line or "RPT_ALINKS" in line:
                nums = re.findall(r'\b(\d{4,7})\b', line)
                status["connected"].extend(nums)
        status["connected"] = list(set(status["connected"]))
        return status


def ami_session():
    """Create and return a connected AMIClient using manager.conf credentials."""
    cfg = parse_manager_conf()
    client = AMIClient(cfg["host"], cfg["port"], cfg["user"], cfg["secret"])
    client.connect()
    return client


# ─── rpt.conf helpers ────────────────────────────────────────────────────────

def read_conf_file(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return None

def write_conf_file(path, content):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"rpt.conf.{ts}.bak")
    if os.path.exists(path):
        shutil.copy2(path, backup)
    with open(path, "w") as f:
        f.write(content)
    return backup

def parse_node_settings(content):
    settings = {}
    for line in content.splitlines():
        stripped = line.strip()
        commented = stripped.startswith(";")
        if commented:
            stripped = stripped[1:].strip()
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;]*?)(?:\s*;.*)?$', stripped)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            settings[k] = {"value": v, "commented": commented, "raw_line": line}
    return settings

def get_node_numbers(content):
    nodes = []
    for line in content.splitlines():
        m = re.match(r'^\[(\d{4,7})\]', line.strip())
        if m:
            nodes.append(m.group(1))
    return nodes

def update_setting_in_content(content, section, key, value, enable=True):
    lines = content.splitlines(keepends=True)
    in_section = found = False
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if re.match(r'^\[.+\]', s):
            m = re.match(r'^\[([^\]\(]+)', s)
            if m:
                in_section = (m.group(1).strip() == section)
        if in_section and not found:
            test = s.lstrip(";").strip()
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', test)
            if m and m.group(1) == key:
                found = True
                result.append(f"{key} = {value}\n" if enable else f";{key} = {value}\n")
                i += 1
                continue
        result.append(line)
        i += 1
    if not found:
        new_lines = []
        in_target = inserted = False
        for line in result:
            s = line.strip()
            if re.match(r'^\[.+\]', s):
                m = re.match(r'^\[([^\]\(]+)', s)
                if m:
                    sec = m.group(1).strip()
                    if in_target and not inserted:
                        pfx = "" if enable else ";"
                        new_lines.append(f"{pfx}{key} = {value}\n")
                        inserted = True
                    in_target = (sec == section)
            new_lines.append(line)
        if in_target and not inserted:
            pfx = "" if enable else ";"
            new_lines.append(f"{pfx}{key} = {value}\n")
        result = new_lines
    return "".join(result)

# ─── astdb lookup ─────────────────────────────────────────────────────────────

_astdb_cache = {}
_astdb_loaded = False

def load_astdb():
    global _astdb_cache, _astdb_loaded
    for path in ASTDB_PATHS:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) >= 3:
                            node = parts[0].strip()
                            callsign = parts[1].strip()
                            desc = parts[2].strip() if len(parts) > 2 else ""
                            location = parts[3].strip() if len(parts) > 3 else ""
                            _astdb_cache[node] = {
                                "callsign": callsign,
                                "desc": desc,
                                "location": location
                            }
                _astdb_loaded = True
                return True
            except:
                pass
    return False

def lookup_node(node):
    if not _astdb_loaded:
        load_astdb()
    return _astdb_cache.get(str(node), {"callsign": "", "desc": "", "location": ""})

# ─── System info ─────────────────────────────────────────────────────────────

def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except:
        pass
    try:
        result = subprocess.run(["vcgencmd", "measure_temp"],
                                capture_output=True, text=True, timeout=3)
        m = re.search(r'[\d.]+', result.stdout)
        if m:
            return float(m.group())
    except:
        pass
    return None

def get_disk_usage():
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            return {"total": parts[1], "used": parts[2], "avail": parts[3], "pct": parts[4]}
    except:
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
    except:
        return "unknown"

def get_asl_version():
    try:
        result = subprocess.run(["dpkg", "-l", "asl3"],
                                capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if line.startswith("ii"):
                return line.split()[2]
    except:
        pass
    return "unknown"

# ─── Flask routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    content = read_conf_file(RPT_CONF_PATH)
    nodes = get_node_numbers(content) if content else []
    return render_template("index.html",
                           conf_exists=content is not None,
                           nodes=nodes,
                           conf_path=RPT_CONF_PATH)

# ── rpt.conf API ──────────────────────────────────────────────────────────────

@app.route("/api/conf")
def api_get_conf():
    content = read_conf_file(RPT_CONF_PATH)
    if content is None:
        return jsonify({"error": "Cannot read rpt.conf", "path": RPT_CONF_PATH}), 404
    return jsonify({
        "content": content,
        "nodes": get_node_numbers(content),
        "general_settings": parse_node_settings(content)
    })

@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.json or {}
    content = read_conf_file(RPT_CONF_PATH) or ""
    raw = data.get("raw_content")
    if raw is not None:
        try:
            backup = write_conf_file(RPT_CONF_PATH, raw)
            return jsonify({"success": True, "backup": backup})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    section = data.get("section", "")
    for key, info in data.get("changes", {}).items():
        content = update_setting_in_content(
            content, section, key,
            info.get("value", ""), enable=info.get("enabled", True))
    try:
        backup = write_conf_file(RPT_CONF_PATH, content)
        return jsonify({"success": True, "backup": backup})
    except PermissionError:
        return jsonify({"error": f"Permission denied: {RPT_CONF_PATH}"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/restart", methods=["POST"])
def api_restart():
    try:
        r = subprocess.run(RESTART_CMD.split(), capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return jsonify({"success": True, "output": r.stdout or "Restarted."})
        return jsonify({"error": r.stderr or "Failed", "returncode": r.returncode}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reload", methods=["POST"])
def api_reload():
    try:
        r = subprocess.run(["asterisk", "-rx", "module reload app_rpt.so"],
                           capture_output=True, text=True, timeout=15)
        return jsonify({"success": True, "output": r.stdout or "Reloaded."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/backups")
def api_backups():
    if not os.path.exists(BACKUP_DIR):
        return jsonify({"backups": []})
    files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith(".bak")], reverse=True)[:10]
    return jsonify({"backups": files, "backup_dir": BACKUP_DIR})

@app.route("/api/backup/<filename>")
def api_get_backup(filename):
    path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(path) or not filename.endswith(".bak"):
        return jsonify({"error": "Not found"}), 404
    with open(path) as f:
        return jsonify({"content": f.read(), "filename": filename})

# ── Favorites API ─────────────────────────────────────────────────────────────

@app.route("/api/favorites")
def api_favorites():
    try:
        db = get_db()
        rows = db.execute("SELECT * FROM favorites ORDER BY id").fetchall()
        favs = [dict(r) for r in rows]
        # Enrich with astdb info
        for fav in favs:
            info = lookup_node(fav["node"])
            fav.update(info)
        return jsonify({"favorites": favs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/favorites/add", methods=["POST"])
def api_fav_add():
    data = request.json or {}
    node = str(data.get("node", "")).strip()
    label = str(data.get("label", "")).strip()
    if not node or not node.isdigit():
        return jsonify({"error": "Invalid node number"}), 400
    if not label:
        info = lookup_node(node)
        label = info.get("callsign") or info.get("desc") or f"Node {node}"
    try:
        db = get_db()
        db.execute("INSERT OR IGNORE INTO favorites (node, label) VALUES (?,?)", (node, label))
        db.commit()
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
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/favorites/label", methods=["POST"])
def api_fav_label():
    data = request.json or {}
    node = str(data.get("node", "")).strip()
    label = str(data.get("label", "")).strip()
    try:
        db = get_db()
        db.execute("UPDATE favorites SET label=? WHERE node=?", (label, node))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Node stats from ASL stats API (proxy to avoid CORS) ──────────────────────

@app.route("/api/nodestats/<node>")
def api_node_stats(node):
    if not node.isdigit():
        return jsonify({"error": "Invalid node"}), 400
    try:
        url = ASL_STATS_URL.format(node)
        req = urlreq.Request(url, headers={"User-Agent": "ASL3-EZ/1.0"})
        with urlreq.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/nodestats/batch", methods=["POST"])
def api_nodestats_batch():
    """Fetch stats for a list of nodes, respecting rate limits."""
    data = request.json or {}
    nodes = data.get("nodes", [])
    results = {}
    for node in nodes[:15]:   # cap at 15 per call
        try:
            url = ASL_STATS_URL.format(node)
            req = urlreq.Request(url, headers={"User-Agent": "ASL3-EZ/1.0"})
            with urlreq.urlopen(req, timeout=6) as resp:
                results[node] = json.loads(resp.read().decode())
        except Exception as e:
            results[node] = {"error": str(e)}
        time.sleep(0.15)   # ~6-7 req/sec, well under the 30/min limit
    return jsonify(results)

# ── AMI node control ──────────────────────────────────────────────────────────

@app.route("/api/ami/status")
def api_ami_status():
    """Get local node connection status via AMI."""
    content = read_conf_file(RPT_CONF_PATH)
    nodes = get_node_numbers(content) if content else []
    if not nodes:
        return jsonify({"error": "No nodes found in rpt.conf"}), 404
    node = request.args.get("node", nodes[0])
    try:
        ami = ami_session()
        status = ami.get_node_status(node)
        ami.close()
        return jsonify({"node": node, **status})
    except Exception as e:
        return jsonify({"error": str(e), "node": node}), 500

@app.route("/api/ami/connect", methods=["POST"])
def api_ami_connect():
    data = request.json or {}
    local_node  = str(data.get("local_node", ""))
    remote_node = str(data.get("remote_node", ""))
    mode        = str(data.get("mode", "3"))          # 3=transceive, 2=monitor
    disc_first  = data.get("disconnect_first", False)
    if not local_node or not remote_node:
        return jsonify({"error": "local_node and remote_node required"}), 400
    try:
        ami = ami_session()
        output = []
        if disc_first:
            out = ami.rpt_cmd(local_node, "ilink 6")
            output.extend(out)
            time.sleep(0.6)
        out = ami.rpt_cmd(local_node, f"ilink {mode} {remote_node}")
        output.extend(out)
        ami.close()
        return jsonify({"success": True, "output": output})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ami/disconnect", methods=["POST"])
def api_ami_disconnect():
    data = request.json or {}
    local_node  = str(data.get("local_node", ""))
    remote_node = str(data.get("remote_node", ""))
    if not local_node:
        return jsonify({"error": "local_node required"}), 400
    try:
        ami = ami_session()
        if remote_node:
            out = ami.rpt_cmd(local_node, f"ilink 1 {remote_node}")
        else:
            out = ami.rpt_cmd(local_node, "ilink 6")
        ami.close()
        return jsonify({"success": True, "output": out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ami/monitor", methods=["POST"])
def api_ami_monitor():
    """Connect in monitor-only mode."""
    data = request.json or {}
    data["mode"] = "2"
    return api_ami_connect.__wrapped__(data) if hasattr(api_ami_connect, '__wrapped__') else api_ami_connect()

@app.route("/api/ami/perm_connect", methods=["POST"])
def api_ami_perm_connect():
    """Permanently connect a node (persists across restarts)."""
    data = request.json or {}
    local_node  = str(data.get("local_node", ""))
    remote_node = str(data.get("remote_node", ""))
    mode        = str(data.get("mode", "13"))   # 13=perm transceive, 12=perm monitor
    if not local_node or not remote_node:
        return jsonify({"error": "local_node and remote_node required"}), 400
    try:
        ami = ami_session()
        out = ami.rpt_cmd(local_node, f"ilink {mode} {remote_node}")
        ami.close()
        return jsonify({"success": True, "output": out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── System info API ───────────────────────────────────────────────────────────

@app.route("/api/sysinfo")
def api_sysinfo():
    return jsonify({
        "cpu_temp":   get_cpu_temp(),
        "disk":       get_disk_usage(),
        "uptime":     get_uptime(),
        "asl_version": get_asl_version(),
        "ami_creds":  parse_manager_conf().get("user", "unknown"),
    })

# ── Node lookup ───────────────────────────────────────────────────────────────

@app.route("/api/lookup/<node>")
def api_lookup(node):
    if not node.isdigit():
        return jsonify({"error": "Invalid node"}), 400
    info = lookup_node(node)
    return jsonify({"node": node, **info})

if __name__ == "__main__":
    load_astdb()
    app.run(host=HOST, port=PORT, debug=False)
