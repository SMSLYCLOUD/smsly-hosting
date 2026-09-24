#!/bin/sh
# SVID fetch for direct mTLS (audit + future service mesh).
# Pulls each workload's X.509 SVID via the mounted SPIRE agent socket and
# normalizes spire's indexed filenames (svid.0.pem/...) to the stable names
# services expect (cert.pem/key.pem/bundle.pem). Files are 644 so the
# non-root app user can present them; the private key never leaves the
# workload container (fetch runs inside it via docker exec).
#
# Cron: */15 * * * * root /opt/smsly-hosting/lib/svid_fetch.sh >> /var/log/svid-fetch.log 2>&1
# Fail-soft per service: one failure never blocks the others.
set -u

SOCK="/opt/spire/run/agent.sock"
DIR="/opt/spire/svids"
BIN="/opt/spire/bin/spire-agent"

# Container names carrying the baked spire-agent binary + spire mounts.
SERVICES="smsly-backend smsly-audit-log-service smsly-identity-service smsly-platform-api smsly-transaction-chain smsly-security-gateway-ojq0s"

fail=0
for c in $SERVICES; do
    if ! docker exec -u root "$c" "$BIN" api fetch x509 \
        -socketPath "$SOCK" -write "$DIR" >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) $c fetch FAILED"
        fail=1
        continue
    fi
    # Normalize only on content change: blind cp would bump mtimes every
    # run and trigger pointless TLS listener restarts downstream.
    if ! docker exec -u root "$c" sh -c \
        'cd /opt/spire/svids && k=$(ls -t svid.*.key 2>/dev/null | head -n 1) && [ -n "$k" ] && n=${k%.key} && b=$(ls -t bundle.*.pem 2>/dev/null | head -n 1) && [ -n "$b" ] && { cmp -s "$n.pem" cert.pem || cp -f "$n.pem" cert.pem; } && { cmp -s "$n.key" key.pem || cp -f "$n.key" key.pem; } && { cmp -s "$b" bundle.pem || cp -f "$b" bundle.pem; } && chmod 644 cert.pem key.pem bundle.pem' \
        >/dev/null 2>&1; then
        echo "$(date -u +%FT%TZ) $c normalize FAILED"
        fail=1
        continue
    fi
    echo "$(date -u +%FT%TZ) $c refreshed"
done
exit $fail
