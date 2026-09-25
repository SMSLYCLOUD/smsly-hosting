#!/bin/bash
# Sync the CoreDNS mesh zone from the master (node-side).
#
# Polls GET /api/v1/servers/<id>/mesh-dns-zone/ with the per-server
# gateway_secret HMAC (same scheme as agent-ready/agent-heartbeat)
# and atomically rewrites infrastructure/docker/coredns-config/
# {Corefile,mesh.hosts}. CoreDNS's `hosts` plugin re-reads on change
# (reload 5s) — no container restart needed.
#
# Fail-closed: any fetch/parse/validation failure keeps the previous
# files untouched and exits nonzero (cron logs it). Never writes
# empty content — an empty zone would blackhole the entire mesh.
#
# Installed as a root cron (*/5) by the bootstrap script; also safe
# to run by hand: scripts/sync-node-mesh-dns.sh
set -u
INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"
ENV_FILE="$INSTALL_DIR/.env"
CONFIG_DIR="$INSTALL_DIR/infrastructure/docker/coredns-config"

if [ ! -f "$ENV_FILE" ]; then
    echo "sync-node-mesh-dns: $ENV_FILE not found" >&2
    exit 1
fi

# shellcheck disable=SC1090
_server_id="$(grep -m1 '^SERVER_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"
_gateway_secret="$(grep -m1 '^GATEWAY_SECRET=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"
_master_url="$(grep -m1 '^MASTER_URL=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r')"
if [ -z "${_server_id:-}" ] || [ -z "${_gateway_secret:-}" ] || [ -z "${_master_url:-}" ]; then
    echo "sync-node-mesh-dns: SERVER_ID/GATEWAY_SECRET/MASTER_URL missing from $ENV_FILE" >&2
    exit 1
fi

mkdir -p "$CONFIG_DIR" || { echo "sync-node-mesh-dns: cannot create $CONFIG_DIR" >&2; exit 1; }

export SMSLY_SYNC_SERVER_ID="$_server_id"
export SMSLY_SYNC_GATEWAY_SECRET="$_gateway_secret"
export SMSLY_SYNC_MASTER_URL="$_master_url"
export SMSLY_SYNC_CONFIG_DIR="$CONFIG_DIR"

/usr/bin/env python3 - <<'PYEOF'
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.request

server_id = os.environ["SMSLY_SYNC_SERVER_ID"]
secret = os.environ["SMSLY_SYNC_GATEWAY_SECRET"]
master = os.environ["SMSLY_SYNC_MASTER_URL"].rstrip("/")
config_dir = os.environ["SMSLY_SYNC_CONFIG_DIR"]

path = f"/api/v1/servers/{server_id}/mesh-dns-zone/"
ts = str(int(time.time()))
nonce = secrets.token_hex(8)
body_hash = hashlib.sha256(b"").hexdigest()
payload = f"GET|{path}|{ts}|{nonce}|{body_hash}"
sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

req = urllib.request.Request(
    master + path,
    headers={
        "X-Gateway-Signature-V2": sig,
        "X-Request-Timestamp": ts,
        "X-Request-Nonce": nonce,
    },
)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception as exc:
    print(f"sync-node-mesh-dns: fetch failed: {exc}", file=sys.stderr)
    sys.exit(1)

hosts = data.get("hosts") or ""
corefile = data.get("corefile") or ""
# Validate: hosts must have at least one record line;
# corefile must contain the hosts plugin stanza.
hosts_lines = [l for l in hosts.splitlines() if l.strip() and not l.strip().startswith("#")]
if len(hosts_lines) < 1:
    print("sync-node-mesh-dns: refusing to write empty zone", file=sys.stderr)
    sys.exit(1)
if "hosts " not in corefile or "mesh.hosts" not in corefile:
    print("sync-node-mesh-dns: corefile failed validation", file=sys.stderr)
    sys.exit(1)


def _atomic_write(path, content):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _current(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


changed = []
if _current(os.path.join(config_dir, "mesh.hosts")) != hosts:
    _atomic_write(os.path.join(config_dir, "mesh.hosts"), hosts)
    changed.append("mesh.hosts")
if _current(os.path.join(config_dir, "Corefile")) != corefile:
    _atomic_write(os.path.join(config_dir, "Corefile"), corefile)
    changed.append("Corefile")
os.chmod(os.path.join(config_dir, "mesh.hosts"), 0o644)
os.chmod(os.path.join(config_dir, "Corefile"), 0o644)
print(f"sync-node-mesh-dns: ok ({data.get('records', '?')} records)"
      + (f" updated: {', '.join(changed)}" if changed else " unchanged"))
PYEOF
