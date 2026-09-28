#!/bin/sh
# SVID fetch for direct mTLS (audit + future service mesh).
# Pulls each workload's X.509 SVID via the mounted SPIRE agent socket and
# normalizes spire's indexed filenames (svid.0.pem/...) to the stable names
# services expect (cert.pem/key.pem/bundle.pem). Files are 644 so the
# non-root app user can present them; the private key never leaves the
# workload container (fetch runs inside it via docker exec).
#
# Per-service subdirs (/opt/spire/svids/<service>/): the shared volume
# previously mixed all SVIDs into one filename set (last-writer-wins
# identity soup — every service presented the same cert). Services read
# their own subdir via SPIFFE_SVID_DIR / SPIFFE_SVID_{CERT,KEY,BUNDLE}_PATH.
# The shared root files are still refreshed for backward compat during
# the env migration; new readers must use the subdir.
#
# Cron: */15 * * * * root /opt/smsly-hosting/lib/svid_fetch.sh >> /var/log/svid-fetch.log 2>&1
# Fail-soft per service: one failure never blocks the others.
set -u

SOCK="/opt/spire/run/agent.sock"
DIR="/opt/spire/svids"
BIN="/opt/spire/bin/spire-agent"

# Container names carrying the baked spire-agent binary + spire mounts.
# Gateway is blue-green suffixed per deploy — resolve it dynamically.
SERVICES="smsly-backend smsly-audit-log-service smsly-identity-service smsly-platform-api smsly-transaction-chain smsly-policy-service smsly-rate-limit-service"
_gateway="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^smsly-security-gateway' | grep -v envoy | head -n 1)"
if [ -n "$_gateway" ]; then
    SERVICES="$SERVICES $_gateway"
fi

# Stable per-service directory: com.paas.service label (canonical across
# blue-green renames), falling back to the container name.
service_dir() {
    _lbl="$(docker inspect -f '{{index .Config.Labels "com.paas.service"}}' "$1" 2>/dev/null)"
    if [ -n "$_lbl" ] && [ "$_lbl" != "<no value>" ]; then
        printf '%s' "$_lbl"
    else
        printf '%s' "$1"
    fi
}

normalize_dir() {
    # $1 = container, $2 = dir inside container
    docker exec -u root "$1" sh -c \
        'cd "$0" && mkdir -p . && chmod 644 svid.*.pem svid.*.key bundle.*.pem 2>/dev/null; k=$(ls -t svid.*.key 2>/dev/null | head -n 1) && [ -n "$k" ] && n=${k%.key} && b=$(ls -t bundle.*.pem 2>/dev/null | head -n 1) && [ -n "$b" ] && { cmp -s "$n.pem" cert.pem || cp -f "$n.pem" cert.pem; } && { cmp -s "$n.key" key.pem || cp -f "$n.key" key.pem; } && { cmp -s "$b" bundle.pem || cp -f "$b" bundle.pem; } && chmod 644 cert.pem key.pem bundle.pem' "$2" \
        >/dev/null 2>&1
}

fail=0
# No workloads here at all (fresh node: edge stack only, no app
# containers yet) — nothing to fetch is success, not failure. Without
# this early-out every run exits 1, which kills strict callers
# (install.sh under set -e died silently mid-install on 2026-09-28)
# and spams the 15-min cron forever on nodes.
svid_fetch_main() {
_found=0
for c in $SERVICES; do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then
        _found=1
        break
    fi
done
if [ "$_found" != "1" ]; then
    echo "$(date -u +%FT%TZ) no workload containers present — nothing to fetch"
    return 0
fi
for c in $SERVICES; do
    _svc="$(service_dir "$c")"
    _subdir="$DIR/$_svc"
    if ! docker exec -u root "$c" sh -c 'mkdir -p "$0"' "$_subdir" >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) $c mkdir FAILED"
        fail=1
        continue
    fi
    if ! docker exec -u root "$c" "$BIN" api fetch x509 \
        -socketPath "$SOCK" -write "$_subdir" >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) $c fetch FAILED"
        fail=1
        continue
    fi
    # Normalize only on content change: blind cp would bump mtimes every
    # run and trigger pointless TLS listener restarts downstream.
    # Also relax the indexed files to 644: app runtimes (non-root) read
    # SVIDs directly and the raw key is root-only 600 (2026-09-26: mesh
    # proxy mTLS EACCES). Keys never leave the workload network scope.
    if ! normalize_dir "$c" "$_subdir"; then
        echo "$(date -u +%FT%TZ) $c normalize FAILED"
        fail=1
        continue
    fi
    # Backward compat: refresh the shared root filenames from this fetch
    # (same last-writer-wins behavior as before) until all readers move
    # to their subdir via env migration.
    if ! docker exec -u root "$c" sh -c \
        'cp -f "$0/cert.pem" "$0/key.pem" "$0/bundle.pem" /opt/spire/svids/ 2>/dev/null && chmod 644 /opt/spire/svids/cert.pem /opt/spire/svids/key.pem /opt/spire/svids/bundle.pem' "$_subdir" \
        >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) $c shared-normalize FAILED"
        fail=1
        continue
    fi
    echo "$(date -u +%FT%TZ) $c refreshed ($_svc)"
done
    return $fail
}

# Source-safe: install.sh sources every lib/*.sh (except fresh/update/
# harden/install-*) for functions. A bare top-level loop + `exit` here
# would run at source time and kill the installer under set -e. Only
# execute when run directly (cron/manual).
case "${0##*/}" in
svid_fetch.sh)
    svid_fetch_main
    exit $?
    ;;
esac
