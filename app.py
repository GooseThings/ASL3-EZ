#!/usr/bin/env python3
"""
ASL3 rpt.conf Web Editor
A simple web interface for editing AllStarLink 3 rpt.conf settings
and restarting Asterisk.
"""

import os
import re
import subprocess
import shutil
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for

app = Flask(__name__)

# --- Configuration ---
RPT_CONF_PATH = os.environ.get("RPT_CONF_PATH", "/etc/asterisk/rpt.conf")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/etc/asterisk/rpt_backups")
RESTART_CMD = os.environ.get("RESTART_CMD", "systemctl restart asterisk")
SECRET_KEY = os.environ.get("SECRET_KEY", "asl3-editor-change-me")
PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "0.0.0.0")

app.secret_key = SECRET_KEY


def read_conf_file(path):
    """Read the rpt.conf file and return its contents as a string."""
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except PermissionError:
        return None


def write_conf_file(path, content):
    """Write the rpt.conf file, creating a backup first."""
    # Ensure backup directory exists
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Create a timestamped backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"rpt.conf.{timestamp}.bak")
    if os.path.exists(path):
        shutil.copy2(path, backup_path)

    with open(path, "w") as f:
        f.write(content)

    return backup_path


def parse_conf_sections(content):
    """
    Parse rpt.conf into sections.
    Returns dict: { section_name: [(key, value, comment, raw_line), ...], '_order': [...] }
    """
    sections = {}
    order = []
    current_section = None
    lines_data = []

    for raw_line in content.splitlines():
        line = raw_line.strip()

        # Section header
        if re.match(r'^\[.+\]', line):
            if current_section is not None:
                sections[current_section] = lines_data
            section_name = re.match(r'^\[([^\]]+)\]', line).group(1)
            # Strip template suffixes like (!)
            section_key = re.sub(r'\s*\(.*\)', '', section_name).strip()
            current_section = section_key
            order.append(current_section)
            lines_data = [{"type": "header", "raw": raw_line, "name": section_name}]
        elif current_section is not None:
            lines_data.append({"type": "line", "raw": raw_line})
        else:
            # Pre-section content
            if "__preamble__" not in sections:
                sections["__preamble__"] = []
                order.insert(0, "__preamble__")
            sections["__preamble__"].append({"type": "line", "raw": raw_line})

    if current_section is not None:
        sections[current_section] = lines_data

    sections["_order"] = order
    return sections


def parse_node_settings(content):
    """
    Parse key=value pairs from the content, returning a dict.
    Handles commented-out settings (prefixed with ;).
    """
    settings = {}
    for line in content.splitlines():
        stripped = line.strip()
        is_commented = stripped.startswith(";")
        if is_commented:
            stripped = stripped[1:].strip()

        # Match key = value ; optional comment
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;]*?)(?:\s*;.*)?$', stripped)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            settings[key] = {
                "value": val,
                "commented": is_commented,
                "raw_line": line
            }
    return settings


def get_node_numbers(content):
    """Extract node number stanzas (numeric section names)."""
    nodes = []
    for line in content.splitlines():
        m = re.match(r'^\[(\d{4,7})\]', line.strip())
        if m:
            nodes.append(m.group(1))
    return nodes


def update_setting_in_content(content, section, key, value, enable=True):
    """
    Update (or add) a key=value in a specific section of the conf content.
    If enable=False, comment the line out.
    Returns updated content string.
    """
    lines = content.splitlines(keepends=True)
    in_section = False
    found = False
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check for section header
        if re.match(r'^\[.+\]', stripped):
            sec_match = re.match(r'^\[([^\]\(]+)', stripped)
            if sec_match:
                sec_name = sec_match.group(1).strip()
                in_section = (sec_name == section)

        if in_section and not found:
            # Check if this line (possibly commented) has our key
            test = stripped.lstrip(";").strip()
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', test)
            if m and m.group(1) == key:
                found = True
                if enable:
                    result.append(f"{key} = {value}\n")
                else:
                    result.append(f";{key} = {value}\n")
                i += 1
                continue

        result.append(line)
        i += 1

    # If not found, append to the end of the section
    if not found:
        new_lines = []
        in_target = False
        inserted = False
        for line in result:
            stripped = line.strip()
            if re.match(r'^\[.+\]', stripped):
                sec_match = re.match(r'^\[([^\]\(]+)', stripped)
                if sec_match:
                    sec_name = sec_match.group(1).strip()
                    if in_target and not inserted:
                        prefix = "" if enable else ";"
                        new_lines.append(f"{prefix}{key} = {value}\n")
                        inserted = True
                    in_target = (sec_name == section)
            new_lines.append(line)

        if in_target and not inserted:
            prefix = "" if enable else ";"
            new_lines.append(f"{prefix}{key} = {value}\n")

        result = new_lines

    return "".join(result)


# ---- Routes ----

@app.route("/")
def index():
    content = read_conf_file(RPT_CONF_PATH)
    exists = content is not None
    nodes = get_node_numbers(content) if content else []
    return render_template("index.html",
                           conf_exists=exists,
                           nodes=nodes,
                           conf_path=RPT_CONF_PATH)


@app.route("/api/conf", methods=["GET"])
def api_get_conf():
    content = read_conf_file(RPT_CONF_PATH)
    if content is None:
        return jsonify({"error": "Cannot read rpt.conf", "path": RPT_CONF_PATH}), 404
    nodes = get_node_numbers(content)
    settings = {}
    for node in nodes:
        settings[node] = parse_node_settings(content)
    general_settings = parse_node_settings(content)
    return jsonify({
        "content": content,
        "nodes": nodes,
        "general_settings": general_settings
    })


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    content = read_conf_file(RPT_CONF_PATH)
    if content is None:
        # If no file, start fresh
        content = ""

    raw_content = data.get("raw_content")
    if raw_content is not None:
        # Save raw content (from the raw editor)
        try:
            backup = write_conf_file(RPT_CONF_PATH, raw_content)
            return jsonify({"success": True, "backup": backup})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Handle structured updates
    changes = data.get("changes", {})
    section = data.get("section", "")

    for key, info in changes.items():
        content = update_setting_in_content(
            content,
            section,
            key,
            info.get("value", ""),
            enable=info.get("enabled", True)
        )

    try:
        backup = write_conf_file(RPT_CONF_PATH, content)
        return jsonify({"success": True, "backup": backup})
    except PermissionError:
        return jsonify({"error": "Permission denied writing to " + RPT_CONF_PATH +
                        ". Try running with sudo or check file permissions."}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/restart", methods=["POST"])
def api_restart():
    try:
        result = subprocess.run(
            RESTART_CMD.split(),
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return jsonify({"success": True, "output": result.stdout or "Asterisk restarted successfully."})
        else:
            return jsonify({
                "error": result.stderr or "Restart command failed",
                "stdout": result.stdout,
                "returncode": result.returncode
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Restart command timed out"}), 500
    except FileNotFoundError:
        return jsonify({"error": f"Command not found: {RESTART_CMD}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reload", methods=["POST"])
def api_reload():
    """Reload app_rpt module without full restart (softer option)."""
    try:
        result = subprocess.run(
            ["asterisk", "-rx", "module reload app_rpt.so"],
            capture_output=True,
            text=True,
            timeout=15
        )
        return jsonify({"success": True, "output": result.stdout or "Module reloaded."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backups", methods=["GET"])
def api_backups():
    if not os.path.exists(BACKUP_DIR):
        return jsonify({"backups": []})
    files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith(".bak")],
        reverse=True
    )[:10]
    return jsonify({"backups": files, "backup_dir": BACKUP_DIR})


@app.route("/api/backup/<filename>", methods=["GET"])
def api_get_backup(filename):
    path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(path) or not filename.endswith(".bak"):
        return jsonify({"error": "Backup not found"}), 404
    with open(path) as f:
        return jsonify({"content": f.read(), "filename": filename})


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
