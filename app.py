from __future__ import annotations

import base64
import hashlib
import ipaddress
import io
import json
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

import qrcode
from flask import Flask, jsonify, render_template, request, session
from nacl.public import PrivateKey
from nacl.bindings import crypto_scalarmult_base

app = Flask(__name__)
app.secret_key = os.environ.get("AWG_SECRET_KEY", "dev-change-me")

DATA_DIR = Path(os.environ.get("AWG_DATA_DIR", str(Path.home() / "awg-web-gui-data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SERVERS_FILE = DATA_DIR / "servers.json"
CLIENTS_FILE = DATA_DIR / "clients.json"
USERS_FILE = DATA_DIR / "users.json"
DB_FILE = DATA_DIR / "awg-web-gui.db"
POLL_INTERVAL = int(os.environ.get("AWG_POLL_INTERVAL", "30"))
ONLINE_THRESHOLD = int(os.environ.get("AWG_ONLINE_THRESHOLD", "180"))
ENABLE_POLLER = os.environ.get("AWG_ENABLE_POLLER", "1") == "1"

DEFAULT_ADMIN = {"username": "admin", "password_hash": hashlib.sha256(b"admin").hexdigest()}

VERSION_DEFAULTS = {
    "1.5": {
        "container": "amnezia-awg",
        "interface": "wg0",
        "config_path": "/opt/amnezia/awg/wg0.conf",
        "port": 8723,
        "show_tools": ["wg", "awg"],
    },
    "2.0": {
        "container": "amnezia-awg2",
        "interface": "awg0",
        "config_path": "/opt/amnezia/awg/awg0.conf",
        "port": 9723,
        "show_tools": ["awg", "wg"],
    },
}

AWG_PARAM_KEYS = ["Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "I1"]
AWG_PARAM_CANON = {k.lower(): k for k in AWG_PARAM_KEYS}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS servers (
              id TEXT PRIMARY KEY,
              data TEXT NOT NULL,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS clients (
              id TEXT PRIMARY KEY,
              server_id TEXT NOT NULL,
              pubkey TEXT,
              data TEXT NOT NULL,
              created_at TEXT,
              updated_at TEXT,
              FOREIGN KEY(server_id) REFERENCES servers(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_clients_server ON clients(server_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_server_pubkey ON clients(server_id, pubkey) WHERE pubkey IS NOT NULL AND pubkey != '';
            CREATE TABLE IF NOT EXISTS users (
              username TEXT PRIMARY KEY,
              password_hash TEXT NOT NULL,
              data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS client_stats (
              client_id TEXT PRIMARY KEY,
              server_id TEXT NOT NULL,
              public_key TEXT NOT NULL,
              endpoint TEXT,
              allowed_ips TEXT,
              latest_handshake INTEGER DEFAULT 0,
              transfer_rx INTEGER DEFAULT 0,
              transfer_tx INTEGER DEFAULT 0,
              last_rx INTEGER DEFAULT 0,
              last_tx INTEGER DEFAULT 0,
              total_rx INTEGER DEFAULT 0,
              total_tx INTEGER DEFAULT 0,
              online INTEGER DEFAULT 0,
              last_seen_at TEXT,
              updated_at TEXT,
              FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_stats_server ON client_stats(server_id);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              kind TEXT NOT NULL,
              server_id TEXT,
              client_id TEXT,
              message TEXT,
              data TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )


def load_servers_from_db() -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT data FROM servers ORDER BY created_at, id").fetchall()
    return {item["id"]: item for item in (json.loads(r["data"]) for r in rows)}


def load_clients_from_db() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT data FROM clients ORDER BY created_at, id").fetchall()
    return [json.loads(r["data"]) for r in rows]


def load_users_from_db() -> list[dict[str, str]]:
    with db() as conn:
        rows = conn.execute("SELECT data FROM users ORDER BY username").fetchall()
    return [json.loads(r["data"]) for r in rows] or [DEFAULT_ADMIN]


def write_event(kind: str, server_id: str | None = None, client_id: str | None = None, message: str = "", data: Any = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO events(ts,kind,server_id,client_id,message,data) VALUES(?,?,?,?,?,?)",
            (now(), kind, server_id, client_id, message, json.dumps(data, ensure_ascii=False) if data is not None else None),
        )


def persist() -> None:
    with db() as conn:
        conn.execute("DELETE FROM servers")
        conn.execute("DELETE FROM clients")
        conn.execute("DELETE FROM users")
        for srv in SERVERS.values():
            conn.execute(
                "INSERT OR REPLACE INTO servers(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                (srv["id"], json.dumps(srv, ensure_ascii=False), srv.get("created_at"), srv.get("updated_at")),
            )
        for client in CLIENTS:
            conn.execute(
                "INSERT OR REPLACE INTO clients(id,server_id,pubkey,data,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (client["id"], client.get("server_id"), client.get("pubkey"), json.dumps(client, ensure_ascii=False), client.get("created_at"), client.get("updated_at")),
            )
        for user in USERS:
            conn.execute(
                "INSERT OR REPLACE INTO users(username,password_hash,data) VALUES(?,?,?)",
                (user["username"], user["password_hash"], json.dumps(user, ensure_ascii=False)),
            )
    # Keep legacy JSON exports for easy backup/debug while SQLite is authoritative.
    save_json(SERVERS_FILE, SERVERS)
    save_json(CLIENTS_FILE, CLIENTS)
    save_json(USERS_FILE, USERS)


def migrate_json_to_sqlite_if_empty() -> None:
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    if count == 0:
        legacy_servers = load_json(SERVERS_FILE, {})
        legacy_clients = load_json(CLIENTS_FILE, [])
        legacy_users = load_json(USERS_FILE, [DEFAULT_ADMIN])
        if legacy_servers or legacy_clients or legacy_users != [DEFAULT_ADMIN]:
            globals()["SERVERS"] = legacy_servers
            globals()["CLIENTS"] = legacy_clients
            globals()["USERS"] = legacy_users
            persist()
            write_event("migration", message="Migrated legacy JSON data to SQLite")


init_db()
migrate_json_to_sqlite_if_empty()
SERVERS: dict[str, dict[str, Any]] = load_servers_from_db()
CLIENTS: list[dict[str, Any]] = load_clients_from_db()
USERS: list[dict[str, str]] = load_users_from_db()


def version_defaults(version: str) -> dict[str, Any]:
    if version not in VERSION_DEFAULTS:
        raise ValueError("version must be 1.5 or 2.0")
    return VERSION_DEFAULTS[version]


def normalize_server(data: dict[str, Any], existing: dict[str, Any] | None = None, server_conf: str | None = None) -> dict[str, Any]:
    existing = existing or {}
    version = str(data.get("version", existing.get("version", "1.5")))
    d = version_defaults(version)
    sid = str(data.get("id") or existing.get("id") or str(uuid.uuid4())[:8])
    subnet_default = existing.get("subnet") or "10.8.0.0/24"
    if server_conf:
        addr = parse_interface_value(server_conf, "Address")
        if addr:
            try:
                subnet_default = str(ipaddress.ip_network(addr, strict=False))
            except Exception:
                pass
    server = {
        "id": sid,
        "name": str(data.get("name", existing.get("name", ""))).strip(),
        "host": str(data.get("host", existing.get("host", ""))).strip(),
        "version": version,
        "ssh_user": str(data.get("ssh_user", existing.get("ssh_user", "root"))).strip() or "root",
        "ssh_port": int(data.get("ssh_port", existing.get("ssh_port", 22)) or 22),
        "ssh_key": str(data.get("ssh_key", existing.get("ssh_key", ""))).strip(),
        "ssh_password": str(data.get("ssh_password", existing.get("ssh_password", ""))).strip(),
        "wg_port": int(data.get("wg_port", existing.get("wg_port", d["port"])) or d["port"]),
        "dns": str(data.get("dns", existing.get("dns", "1.1.1.1,8.8.8.8"))).strip(),
        "endpoint": str(data.get("endpoint", existing.get("endpoint", ""))).strip(),
        "subnet": str(data.get("subnet", subnet_default)).strip(),
        "container": str(data.get("container", existing.get("container", d["container"]))).strip() or d["container"],
        "interface": str(data.get("interface", existing.get("interface", d["interface"]))).strip() or d["interface"],
        "config_path": str(data.get("config_path", existing.get("config_path", d["config_path"]))).strip() or d["config_path"],
        "created_at": existing.get("created_at") or now(),
        "updated_at": now(),
    }
    if not server["endpoint"]:
        server["endpoint"] = server["host"]
    return server


def resolve_ssh_key_path(path: str) -> str:
    """Resolve common host-path mistakes inside Docker.

    The container only sees keys mounted under /ssh. If the user enters a host
    path like /root/.ssh/id_ed25519, try /ssh/id_ed25519 automatically.
    """
    path = (path or "").strip()
    if not path:
        return ""
    if os.path.exists(path):
        return path
    alt = "/ssh/" + os.path.basename(path)
    if path.startswith("/root/.ssh/") and os.path.exists(alt):
        return alt
    return path


def ssh_base_cmd(server: dict[str, Any]) -> tuple[list[str], dict[str, str] | None]:
    password = str(server.get("ssh_password") or "")
    key_path = resolve_ssh_key_path(str(server.get("ssh_key") or ""))
    ssh = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=7",
        "-p", str(server.get("ssh_port", 22)),
    ]
    if password:
        ssh = ["sshpass", "-e"] + ssh
        ssh += ["-o", "PreferredAuthentications=password,keyboard-interactive,publickey"]
        env = {**os.environ, "SSHPASS": password}
    else:
        ssh += ["-o", "BatchMode=yes"]
        env = None
    if key_path:
        ssh += ["-i", key_path]
    ssh.append(f"{server.get('ssh_user', 'root')}@{server['host']}")
    return ssh, env


def ssh_run(server: dict[str, Any], command: str, timeout: int = 25) -> dict[str, Any]:
    try:
        cmd, env = ssh_base_cmd(server)
        r = subprocess.run(cmd + [command], capture_output=True, text=True, timeout=timeout, env=env)
        err = r.stderr
        if server.get("ssh_key") and resolve_ssh_key_path(str(server.get("ssh_key"))) != server.get("ssh_key"):
            err = f"Info: SSH key path mapped {server.get('ssh_key')} -> {resolve_ssh_key_path(str(server.get('ssh_key')))}\n" + err
        return {"ok": r.returncode == 0, "code": r.returncode, "out": r.stdout, "err": err}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": "timeout"}
    except Exception as e:
        return {"ok": False, "code": -1, "out": "", "err": str(e)}

def q(s: str) -> str:
    return shlex.quote(str(s))


def cexec(server: dict[str, Any], inner: str, timeout: int = 25) -> dict[str, Any]:
    return ssh_run(server, f"docker exec {q(server['container'])} sh -lc {q(inner)}", timeout=timeout)


def read_server_conf(server: dict[str, Any]) -> str:
    r = cexec(server, f"cat {q(server['config_path'])}")
    return r["out"] if r["ok"] else ""


def backup_server_conf(server: dict[str, Any]) -> dict[str, Any]:
    path = server["config_path"]
    return cexec(server, f"test -f {q(path)} && cp {q(path)} {q(path)}.bak-$(date +%Y%m%d-%H%M%S)")


def write_server_conf(server: dict[str, Any], content: str) -> dict[str, Any]:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    path = server["config_path"]
    inner = f"base64 -d > {q(path)}"
    cmd = f"printf %s {q(b64)} | docker exec -i {q(server['container'])} sh -lc {q(inner)} && docker restart {q(server['container'])} >/dev/null"
    return ssh_run(server, cmd, timeout=90)


def show_command(server: dict[str, Any]) -> str:
    tool = "awg" if server["version"] == "2.0" else "wg"
    return f"{tool} show {q(server['interface'])}"


def runtime_show(server: dict[str, Any]) -> dict[str, Any]:
    tools = version_defaults(server["version"])["show_tools"]
    last = {"ok": False, "out": "", "err": "not tried"}
    for tool in tools:
        last = cexec(server, f"{tool} show {q(server['interface'])}")
        if last["ok"]:
            return last
    return last


def validate_server(server: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["ssh"] = ssh_run(server, "echo ok", timeout=12)
    if not checks["ssh"]["ok"]:
        return {"ok": False, "stage": "ssh", "checks": checks}
    checks["container"] = ssh_run(server, f"docker inspect -f '{{{{.State.Running}}}}' {q(server['container'])}")
    if not checks["container"]["ok"] or "true" not in checks["container"]["out"].lower():
        return {"ok": False, "stage": "container", "checks": checks}
    checks["config"] = cexec(server, f"test -f {q(server['config_path'])} && echo ok")
    if not checks["config"]["ok"]:
        return {"ok": False, "stage": "config", "checks": checks}
    checks["show"] = runtime_show(server)
    return {"ok": checks["show"]["ok"], "stage": "show" if not checks["show"]["ok"] else "ok", "checks": checks}


def parse_interface_params(conf: str) -> dict[str, str]:
    section = None
    params: dict[str, str] = {}
    for raw in conf.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "interface" and "=" in line:
            k, v = line.split("=", 1)
            ck = AWG_PARAM_CANON.get(k.strip().lower())
            if ck:
                params[ck] = v.strip()
    return params


def parse_interface_value(conf: str, key: str) -> str:
    section = None
    for raw in conf.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "interface" and "=" in line:
            k, v = line.split("=", 1)
            if k.strip().lower() == key.lower():
                return v.strip()
    return ""


def server_access_base(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(data.get("id") or "").strip(),
        "name": str(data.get("name", "")).strip(),
        "host": str(data.get("host", "")).strip(),
        "ssh_user": str(data.get("ssh_user", "root")).strip() or "root",
        "ssh_port": int(data.get("ssh_port", 22) or 22),
        "ssh_key": str(data.get("ssh_key", "")).strip(),
        "ssh_password": str(data.get("ssh_password", "")).strip(),
        "endpoint": str(data.get("endpoint", "")).strip(),
        "dns": str(data.get("dns", "")).strip(),
    }


def build_detected_server(base: dict[str, Any], version: str, conf: str) -> dict[str, Any]:
    d = version_defaults(version)
    data = dict(base)
    data["version"] = version
    data["container"] = d["container"]
    data["interface"] = d["interface"]
    data["config_path"] = d["config_path"]

    listen_port = parse_interface_value(conf, "ListenPort")
    if listen_port:
        data["wg_port"] = int(listen_port)

    address = parse_interface_value(conf, "Address")
    if address:
        data["subnet"] = str(ipaddress.ip_network(address.split(",", 1)[0].strip(), strict=False))

    dns = parse_interface_value(conf, "DNS") or data.get("dns") or "1.1.1.1,8.8.8.8"
    data["dns"] = dns
    if not data.get("endpoint"):
        data["endpoint"] = data.get("host", "")
    return normalize_server(data, server_conf=conf)


def refresh_server_metadata_from_remote(server: dict[str, Any]) -> dict[str, Any]:
    """Refresh subnet/listen port/DNS from the live AWG config without mutating remote."""
    conf = read_server_conf(server)
    if not conf:
        return server
    refreshed = build_detected_server(server, server["version"], conf)
    # Preserve stable identity and friendly name.
    refreshed["id"] = server["id"]
    refreshed["name"] = server.get("name") or refreshed["name"]
    server.update(refreshed)
    return server


def detect_awg_servers(data: dict[str, Any]) -> list[dict[str, Any]]:
    base = server_access_base(data)
    if not base.get("name") or not base.get("host"):
        raise ValueError("name and host are required")

    detected: list[dict[str, Any]] = []
    errors: dict[str, Any] = {}
    # Prefer 2.0 first in the UI because that is the current target stack.
    for version in ["2.0", "1.5"]:
        d = version_defaults(version)
        probe = normalize_server({**base, "version": version, "container": d["container"], "interface": d["interface"], "config_path": d["config_path"]})
        r = cexec(probe, f"test -f {q(d['config_path'])} && cat {q(d['config_path'])}", timeout=25)
        if not r["ok"] or not r["out"].strip():
            errors[version] = r
            continue
        srv = build_detected_server(base, version, r["out"])
        detected.append(srv)

    if not detected:
        raise RuntimeError("No supported AWG containers/configs detected", errors)

    if len(detected) > 1:
        for srv in detected:
            suffix = "awg 2.0" if srv["version"] == "2.0" else "legacy 1.5"
            if suffix not in srv["name"].lower():
                srv["name"] = f"{srv['name']} {suffix}"
            # Make IDs unique when a single host expands to both variants.
            if not data.get("id"):
                srv["id"] = str(uuid.uuid4())[:8]
    return detected


def parse_peer_blocks(conf: str) -> list[dict[str, Any]]:
    peers: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    comments: list[str] = []
    section = None
    for raw in conf.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            comments.append(stripped)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if current and current.get("PublicKey"):
                peers.append(current)
            section = stripped[1:-1].strip().lower()
            current = {"comments": comments[:]} if section == "peer" else None
            comments.clear()
            continue
        if section == "peer" and current is not None and "=" in stripped:
            k, v = stripped.split("=", 1)
            current[k.strip()] = v.strip()
    if current and current.get("PublicKey"):
        peers.append(current)
    return peers


def remove_peer_block(conf: str, pubkey: str) -> str:
    blocks: list[tuple[str, list[str]]] = []
    current_name = "preamble"
    current: list[str] = []
    for raw in conf.splitlines(keepends=True):
        if raw.strip().startswith("[") and raw.strip().endswith("]"):
            blocks.append((current_name, current))
            current_name = raw.strip()[1:-1].strip().lower()
            current = [raw]
        else:
            current.append(raw)
    blocks.append((current_name, current))
    kept: list[str] = []
    for name, lines in blocks:
        if name == "peer" and any(line.strip().startswith("PublicKey") and pubkey in line for line in lines):
            continue
        kept.extend(lines)
    text = "".join(kept).rstrip() + "\n"
    return text


def server_peer_allowed_ips(address: str) -> str:
    # Server-side peer route must be the client's tunnel address, not 0.0.0.0/0.
    first = address.split(",", 1)[0].strip()
    iface = ipaddress.ip_interface(first)
    mask = 32 if iface.version == 4 else 128
    return f"{iface.ip}/{mask}"


def append_peer_block(conf: str, client: dict[str, Any]) -> str:
    conf = remove_peer_block(conf, client["pubkey"]).rstrip() + "\n\n"
    lines = [
        f"# awg-web-gui name={client.get('name','')} id={client.get('id','')}",
        "[Peer]",
        f"PublicKey = {client['pubkey']}",
        f"AllowedIPs = {server_peer_allowed_ips(client['address'])}",
    ]
    if client.get("preshared_key"):
        lines.append(f"PresharedKey = {client['preshared_key']}")
    return conf + "\n".join(lines) + "\n"


def generate_keypair() -> tuple[str, str]:
    priv = PrivateKey.generate()
    return base64.b64encode(priv.encode()).decode(), base64.b64encode(priv.public_key.encode()).decode()


def public_from_private(privkey: str) -> str:
    raw = base64.b64decode(privkey.strip())
    return base64.b64encode(crypto_scalarmult_base(raw)).decode()


def read_clients_table(server: dict[str, Any]) -> list[dict[str, Any]]:
    cfg_dir = os.path.dirname(server.get("config_path") or "/opt/amnezia/awg/wg0.conf")
    r = cexec(server, f"test -f {q(cfg_dir + '/clientsTable')} && cat {q(cfg_dir + '/clientsTable')} || true")
    if not r["ok"] or not r["out"].strip():
        return []
    try:
        data = json.loads(r["out"])
    except Exception:
        return []
    if isinstance(data, dict):
        data = list(data.values())
    return data if isinstance(data, list) else []


def private_keys_from_clients_table(server: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in read_clients_table(server):
        if not isinstance(item, dict):
            continue
        priv = str(item.get("privateKey") or item.get("private_key") or item.get("clientPrivateKey") or item.get("privkey") or "").strip()
        pub = str(item.get("publicKey") or item.get("public_key") or item.get("clientPublicKey") or item.get("pubkey") or "").strip()
        if priv and not pub:
            try:
                pub = public_from_private(priv)
            except Exception:
                pub = ""
        if pub:
            result[pub] = {
                "privkey": priv,
                "preshared_key": str(item.get("presharedKey") or item.get("preshared_key") or item.get("psk") or "").strip(),
                "name": str(item.get("name") or item.get("clientName") or "").strip(),
            }
    return result


def generate_psk() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def next_client_ip(server: dict[str, Any]) -> str:
    net = ipaddress.ip_network(server.get("subnet") or "10.8.0.0/24", strict=False)
    used = set()
    for c in CLIENTS:
        if c.get("server_id") != server["id"]:
            continue
        try:
            used.add(ipaddress.ip_interface(c.get("address", "").split(",", 1)[0].strip()).ip)
        except Exception:
            pass
    for ip in net.hosts():
        # .1 is usually server address; start at next free host.
        if int(ip) == int(net.network_address) + 1:
            continue
        if ip not in used:
            return f"{ip}/{net.prefixlen}"
    raise RuntimeError("no free IPs in subnet")


def get_server_pubkey(server: dict[str, Any], conf: str | None = None) -> str:
    conf = conf if conf is not None else read_server_conf(server)
    public = parse_interface_value(conf, "PublicKey")
    if public:
        return public
    private = parse_interface_value(conf, "PrivateKey")
    if not private:
        return ""
    cmd = f"printf %s {q(private)} | wg pubkey"
    r = cexec(server, cmd)
    if r["ok"] and r["out"].strip():
        return r["out"].strip()
    r = cexec(server, f"printf %s {q(private)} | awg pubkey")
    return r["out"].strip() if r["ok"] else ""


def get_server_params(server: dict[str, Any]) -> dict[str, Any]:
    conf = read_server_conf(server)
    return {"pubkey": get_server_pubkey(server, conf), "params": parse_interface_params(conf)}


def build_client_conf(server: dict[str, Any], client: dict[str, Any], sp: dict[str, Any]) -> str:
    params = sp.get("params", {}) or {}
    lines = [
        "[Interface]",
        f"PrivateKey = {client['privkey']}",
        f"Address = {client['address']}",
    ]
def build_client_conf(server: dict[str, Any], client: dict[str, Any], sp: dict[str, Any]) -> str:
    params = sp.get("params", {}) or {}
    lines = [
        "[Interface]",
        f"PrivateKey = {client['privkey']}",
        f"Address = {client['address']}",
    ]
    if server.get("dns"):
        lines.append(f"DNS = {server['dns']}")
    for key in AWG_PARAM_KEYS:
        if key in params:
            lines.append(f"{key} = {params[key]}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {sp.get('pubkey','')}",
    ]
    if client.get("preshared_key"):
        lines.append(f"PresharedKey = {client['preshared_key']}")
    lines += [
        f"AllowedIPs = {client.get('allowed_ips','0.0.0.0/0')}",
        f"Endpoint = {server.get('endpoint') or server['host']}:{server['wg_port']}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines) + "\n"


def runtime_show(server: dict[str, Any], retries: int = 3, delay: float = 1.0) -> dict[str, Any]:
    tools = version_defaults(server["version"])["show_tools"]
    import time
    last = {"ok": False, "out": "", "err": "not tried"}
    for attempt in range(retries):
        for tool in tools:
            last = cexec(server, f"{tool} show {q(server['interface'])}")
            if last["ok"]:
                return last
        if attempt < retries - 1:
            time.sleep(delay)
    return last


def wait_for_peer(server: dict[str, Any], pubkey: str, remove: bool = False, timeout: int = 25, delay: float = 1.0) -> dict[str, Any]:
    import time
    deadline = time.time() + timeout
    last = {"ok": False, "out": "", "err": "not tried"}
    while time.time() < deadline:
        tools = version_defaults(server["version"])["show_tools"]
        for tool in tools:
            last = cexec(server, f"{tool} show {q(server['interface'])}")
            if last["ok"]:
                out = last.get("out", "")
                found = pubkey in out
                if remove and not found:
                    return {"ok": True, "stage": "verify_remove", "detail": last}
                if not remove and found:
                    return {"ok": True, "stage": "verify_add", "detail": last, "present": True}
        time.sleep(delay)
    return {"ok": False, "stage": "verify_timeout", "detail": last}


def apply_client_to_server(server: dict[str, Any], client: dict[str, Any], remove: bool = False) -> dict[str, Any]:
    valid = validate_server(server)
    if not valid["ok"]:
        return {"ok": False, "stage": "validate", "detail": valid}
    conf = read_server_conf(server)
    if not conf:
        return {"ok": False, "stage": "read_conf", "err": "empty or unreadable config"}
    backup = backup_server_conf(server)
    if not backup["ok"]:
        return {"ok": False, "stage": "backup", "detail": backup}
    new_conf = remove_peer_block(conf, client["pubkey"]) if remove else append_peer_block(conf, client)
    wr = write_server_conf(server, new_conf)
    if not wr["ok"]:
        return {"ok": False, "stage": "write_restart", "detail": wr}
    return wait_for_peer(server, client["pubkey"], remove=remove, timeout=30, delay=1.0)


def parse_wg_dump(dump: str, now_ts: int | None = None, online_threshold: int = ONLINE_THRESHOLD) -> list[dict[str, Any]]:
    """Parse `wg show <iface> dump` / `awg show <iface> dump` output.

    Returns peer rows only. First dump row is interface metadata.
    """
    now_ts = int(now_ts or time.time())
    peers: list[dict[str, Any]] = []
    for raw in dump.splitlines():
        if not raw.strip():
            continue
        cols = raw.rstrip("\n").split("\t")
        if len(cols) < 8:
            continue
        # Interface row has private key, public key, listen port, fwmark.
        if len(cols) == 4:
            continue
        pubkey, psk, endpoint, allowed_ips, handshake, rx, tx, keepalive = cols[:8]
        try:
            hs = int(handshake or 0)
            rx_i = int(rx or 0)
            tx_i = int(tx or 0)
        except ValueError:
            continue
        online = bool(hs and (now_ts - hs) <= online_threshold)
        peers.append({
            "public_key": pubkey,
            "endpoint": "" if endpoint == "(none)" else endpoint,
            "allowed_ips": allowed_ips,
            "latest_handshake": hs,
            "transfer_rx": rx_i,
            "transfer_tx": tx_i,
            "online": online,
            "last_seen_at": datetime.fromtimestamp(hs).isoformat(timespec="seconds") if hs else "",
        })
    return peers


def compute_counter_delta(old: int, new: int) -> int:
    old = int(old or 0)
    new = int(new or 0)
    return new - old if new >= old else new


def stats_by_client_id() -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM client_stats").fetchall()
    return {r["client_id"]: dict(r) for r in rows}


def update_client_stat(server: dict[str, Any], client: dict[str, Any], peer: dict[str, Any]) -> dict[str, Any]:
    cid = client["id"]
    with db() as conn:
        old = conn.execute("SELECT * FROM client_stats WHERE client_id=?", (cid,)).fetchone()
        old_rx = old["last_rx"] if old else 0
        old_tx = old["last_tx"] if old else 0
        total_rx = (old["total_rx"] if old else 0) + compute_counter_delta(old_rx, peer["transfer_rx"])
        total_tx = (old["total_tx"] if old else 0) + compute_counter_delta(old_tx, peer["transfer_tx"])
        row = {
            "client_id": cid,
            "server_id": server["id"],
            "public_key": peer["public_key"],
            "endpoint": peer.get("endpoint", ""),
            "allowed_ips": peer.get("allowed_ips", ""),
            "latest_handshake": int(peer.get("latest_handshake") or 0),
            "transfer_rx": int(peer.get("transfer_rx") or 0),
            "transfer_tx": int(peer.get("transfer_tx") or 0),
            "last_rx": int(peer.get("transfer_rx") or 0),
            "last_tx": int(peer.get("transfer_tx") or 0),
            "total_rx": total_rx,
            "total_tx": total_tx,
            "online": 1 if peer.get("online") else 0,
            "last_seen_at": peer.get("last_seen_at", ""),
            "updated_at": now(),
        }
        conn.execute(
            """
            INSERT INTO client_stats(client_id,server_id,public_key,endpoint,allowed_ips,latest_handshake,transfer_rx,transfer_tx,last_rx,last_tx,total_rx,total_tx,online,last_seen_at,updated_at)
            VALUES(:client_id,:server_id,:public_key,:endpoint,:allowed_ips,:latest_handshake,:transfer_rx,:transfer_tx,:last_rx,:last_tx,:total_rx,:total_tx,:online,:last_seen_at,:updated_at)
            ON CONFLICT(client_id) DO UPDATE SET
              endpoint=excluded.endpoint, allowed_ips=excluded.allowed_ips, latest_handshake=excluded.latest_handshake,
              transfer_rx=excluded.transfer_rx, transfer_tx=excluded.transfer_tx, last_rx=excluded.last_rx, last_tx=excluded.last_tx,
              total_rx=excluded.total_rx, total_tx=excluded.total_tx, online=excluded.online, last_seen_at=excluded.last_seen_at,
              updated_at=excluded.updated_at
            """,
            row,
        )
    return row


def poll_server_stats(server: dict[str, Any]) -> dict[str, Any]:
    tools = version_defaults(server["version"])["show_tools"]
    result = {"ok": False, "server_id": server["id"], "updated": 0, "error": "not tried"}
    dump = None
    for tool in tools:
        r = cexec(server, f"{tool} show {q(server['interface'])} dump", timeout=20)
        if r["ok"]:
            dump = r["out"]
            break
        result["error"] = r.get("err") or r.get("out") or "dump failed"
    if dump is None:
        write_event("poll_error", server_id=server["id"], message=result["error"])
        return result
    peers = parse_wg_dump(dump)
    clients_by_pub = {c.get("pubkey"): c for c in CLIENTS if c.get("server_id") == server["id"] and c.get("pubkey")}
    updated = 0
    for peer in peers:
        client = clients_by_pub.get(peer["public_key"])
        if client:
            update_client_stat(server, client, peer)
            updated += 1
    write_event("poll", server_id=server["id"], message=f"Updated {updated} client stats", data={"peers": len(peers), "updated": updated})
    return {"ok": True, "server_id": server["id"], "peers": len(peers), "updated": updated}


def poll_all_stats_once() -> list[dict[str, Any]]:
    results = []
    for server in list(SERVERS.values()):
        results.append(poll_server_stats(server))
    return results


def poll_loop() -> None:
    while True:
        try:
            poll_all_stats_once()
        except Exception as e:
            try:
                write_event("poll_error", message=str(e))
            except Exception:
                pass
        time.sleep(max(5, POLL_INTERVAL))


def start_poller_once() -> None:
    if not ENABLE_POLLER or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    # Avoid Flask reloader double-start and allow disabling in tests.
    if getattr(app, "_awg_poller_started", False):
        return
    app._awg_poller_started = True
    threading.Thread(target=poll_loop, name="awg-stats-poller", daemon=True).start()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/me")
def me():
    return jsonify({"logged_in": bool(session.get("logged_in")), "username": session.get("username")})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    h = hashlib.sha256(str(data.get("password", "")).encode()).hexdigest()
    u = next((x for x in USERS if x["username"] == data.get("username") and x["password_hash"] == h), None)
    if not u:
        return jsonify({"ok": False, "error": "bad credentials"}), 401
    session["logged_in"] = True
    session["username"] = u["username"]
    return jsonify({"ok": True})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/servers", methods=["GET"])
@login_required
def list_servers():
    return jsonify(SERVERS)


@app.route("/api/servers", methods=["POST"])
@login_required
def add_server():
    data = request.json or {}
    try:
        if not data.get("version"):
            servers = detect_awg_servers(data)
            created = []
            for s in servers:
                if s["id"] in SERVERS:
                    continue
                SERVERS[s["id"]] = s
                created.append(s)
            persist()
            return jsonify({"ok": True, "server": created[0] if created else servers[0], "servers": created, "detected": servers})

        s = normalize_server(data)
        if not s["name"] or not s["host"]:
            return jsonify({"ok": False, "error": "name and host are required"}), 400
        if s["id"] in SERVERS:
            return jsonify({"ok": False, "error": "id exists"}), 400
        server_conf = None
        if data.get("validate", True):
            v = validate_server(s)
            if not v["ok"]:
                return jsonify({"ok": False, "error": "server validation failed", "validation": v}), 400
            server_conf = read_server_conf(s)
        if not data.get("subnet") and server_conf:
            s = normalize_server(data, server_conf=server_conf)
        SERVERS[s["id"]] = s
        persist()
        return jsonify({"ok": True, "server": s, "servers": [s]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/servers/<sid>", methods=["PUT"])
@login_required
def update_server(sid):
    if sid not in SERVERS:
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        s = normalize_server(request.json or {}, SERVERS[sid])
        s["id"] = sid
        SERVERS[sid] = s
        persist()
        return jsonify({"ok": True, "server": s})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/servers/<sid>", methods=["DELETE"])
@login_required
def delete_server(sid):
    if sid not in SERVERS:
        return jsonify({"ok": False, "error": "not found"}), 404
    # Manager-only delete: never remove real remote containers/configs here.
    global CLIENTS
    CLIENTS = [c for c in CLIENTS if c.get("server_id") != sid]
    del SERVERS[sid]
    persist()
    return jsonify({"ok": True})


@app.route("/api/servers/<sid>/health")
@login_required
def server_health(sid):
    s = SERVERS.get(sid)
    if not s:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify(validate_server(s))


@app.route("/api/servers/<sid>/sync", methods=["POST"])
@login_required
def sync_server(sid):
    s = SERVERS.get(sid)
    if not s:
        return jsonify({"ok": False, "error": "not found"}), 404
    conf = read_server_conf(s)
    if not conf:
        return jsonify({"ok": False, "error": "cannot read server config"}), 502
    peers = parse_peer_blocks(conf)
    known_private = private_keys_from_clients_table(s)
    runtime = runtime_show(s)
    added = 0
    enriched = 0
    by_pub = {c.get("pubkey"): c for c in CLIENTS if c.get("server_id") == sid}
    for p in peers:
        pub = p.get("PublicKey")
        if not pub:
            continue
        meta = known_private.get(pub, {})
        if pub in by_pub:
            by_pub[pub]["server_allowed_ips"] = p.get("AllowedIPs", "")
            if meta.get("privkey") and not by_pub[pub].get("privkey"):
                by_pub[pub]["privkey"] = meta["privkey"]
                enriched += 1
            if meta.get("preshared_key") and not by_pub[pub].get("preshared_key"):
                by_pub[pub]["preshared_key"] = meta["preshared_key"]
        else:
            CLIENTS.append({
                "id": str(uuid.uuid4())[:8],
                "server_id": sid,
                "name": meta.get("name") or f"existing_{pub[:8]}",
                "privkey": meta.get("privkey", ""),
                "pubkey": pub,
                "preshared_key": meta.get("preshared_key") or p.get("PresharedKey", ""),
                "address": p.get("AllowedIPs", ""),
                "server_allowed_ips": p.get("AllowedIPs", ""),
                "allowed_ips": "0.0.0.0/0",
                "created_at": now(),
                "_from_server": True,
            })
            added += 1
    persist()
    return jsonify({"ok": True, "peers": len(peers), "added": added, "enriched_private_keys": enriched, "private_key_source": "clientsTable", "runtime_ok": runtime["ok"]})


@app.route("/api/clients")
@login_required
def list_clients():
    result = []
    stats = stats_by_client_id()
    for c in CLIENTS:
        item = dict(c)
        item["server_name"] = SERVERS.get(c.get("server_id"), {}).get("name", "?")
        item["stats"] = stats.get(c.get("id"), {})
        result.append(item)
    return jsonify(result)


@app.route("/api/clients", methods=["POST"])
@login_required
def add_client():
    data = request.json or {}
    sid = data.get("server_id")
    s = SERVERS.get(sid)
    if not s:
        return jsonify({"ok": False, "error": "server not found"}), 404
    try:
        refresh_server_metadata_from_remote(s)
        priv, pub = generate_keypair()
        address = data.get("address") or next_client_ip(s)
        # Validate early.
        server_peer_allowed_ips(address)
        client = {
            "id": str(data.get("id") or str(uuid.uuid4())[:8]),
            "server_id": sid,
            "name": str(data.get("name") or "client").strip(),
            "privkey": priv,
            "pubkey": pub,
            "preshared_key": str(data.get("preshared_key") or generate_psk()),
            "address": address,
            "allowed_ips": str(data.get("allowed_ips") or "0.0.0.0/0, ::/0"),
            "created_at": now(),
        }
        if not client["name"]:
            return jsonify({"ok": False, "error": "client name is required"}), 400
        apply = apply_client_to_server(s, client, remove=False)
        if not apply["ok"]:
            return jsonify({"ok": False, "error": "failed to apply peer to server", "apply": apply}), 502
        CLIENTS.append(client)
        persist()
        return jsonify({"ok": True, "client": client, "apply": apply})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/clients/<cid>", methods=["PUT"])
@login_required
def update_client(cid):
    client = next((c for c in CLIENTS if c.get("id") == cid), None)
    if not client:
        return jsonify({"ok": False, "error": "not found"}), 404
    data = request.json or {}
    updated = dict(client)
    for k in ["name", "allowed_ips", "address", "preshared_key", "privkey"]:
        if k in data:
            updated[k] = data[k]
    if data.get("privkey") and not updated.get("pubkey"):
        updated["pubkey"] = public_from_private(data["privkey"])
    s = SERVERS.get(updated.get("server_id"))
    if s and updated.get("pubkey"):
        apply = apply_client_to_server(s, updated, remove=False)
        if not apply["ok"]:
            return jsonify({"ok": False, "error": "failed to update server peer", "apply": apply}), 502
    client.update(updated)
    persist()
    return jsonify({"ok": True, "client": client})


@app.route("/api/clients/<cid>", methods=["DELETE"])
@login_required
def delete_client(cid):
    client = next((c for c in CLIENTS if c.get("id") == cid), None)
    if not client:
        return jsonify({"ok": False, "error": "not found"}), 404
    s = SERVERS.get(client.get("server_id"))
    if s and client.get("pubkey"):
        apply = apply_client_to_server(s, client, remove=True)
        if not apply["ok"]:
            return jsonify({"ok": False, "error": "failed to remove peer from server", "apply": apply}), 502
    CLIENTS.remove(client)
    persist()
    return jsonify({"ok": True})


@app.route("/api/stats")
@login_required
def all_stats():
    return jsonify(stats_by_client_id())


@app.route("/api/servers/<sid>/stats")
@login_required
def server_stats(sid):
    with db() as conn:
        rows = conn.execute("SELECT * FROM client_stats WHERE server_id=?", (sid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/servers/<sid>/poll", methods=["POST"])
@login_required
def poll_server_now(sid):
    s = SERVERS.get(sid)
    if not s:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify(poll_server_stats(s))


@app.route("/api/poll", methods=["POST"])
@login_required
def poll_now():
    return jsonify({"ok": True, "results": poll_all_stats_once()})


@app.route("/api/events")
@login_required
def list_events():
    limit = min(int(request.args.get("limit", "100")), 500)
    with db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/clients/<cid>/config")
@login_required
def client_config(cid):
    client = next((c for c in CLIENTS if c.get("id") == cid), None)
    if not client:
        return jsonify({"ok": False, "error": "not found"}), 404
    if not client.get("privkey"):
        return jsonify({"ok": False, "error": "private key unavailable for synced existing client"}), 400
    s = SERVERS.get(client.get("server_id"))
    if not s:
        return jsonify({"ok": False, "error": "server not found"}), 404
    cfg = build_client_conf(s, client, get_server_params(s))
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", client.get("name", "client"))
    return cfg, 200, {"Content-Type": "text/plain; charset=utf-8", "Content-Disposition": f"attachment; filename=awg_{safe_name}.conf"}


@app.route("/api/clients/<cid>/qr.png")
@login_required
def client_qr(cid):
    client = next((c for c in CLIENTS if c.get("id") == cid), None)
    if not client:
        return jsonify({"ok": False, "error": "not found"}), 404
    s = SERVERS.get(client.get("server_id"))
    if not s:
        return jsonify({"ok": False, "error": "server not found"}), 404
    if not client.get("privkey"):
        return jsonify({"ok": False, "error": "private key unavailable for synced existing client"}), 400
    cfg = build_client_conf(s, client, get_server_params(s))
    img = qrcode.make(cfg)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), 200, {"Content-Type": "image/png"}


start_poller_once()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5173")), debug=os.environ.get("FLASK_DEBUG", "0") == "1")
