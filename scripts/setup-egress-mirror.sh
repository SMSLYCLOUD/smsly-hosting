#!/usr/bin/env bash
# scripts/setup-egress-mirror.sh — host egress mirror for CDN-blocked networks.
#
# Installs a local nginx that serves dl-cdn.alpinelinux.org requests from
# working vendor mirrors, and steers port-80 TCP to it. Required on
# networks where the Alpine (and potentially Debian) CDNs are
# unreachable — without it every Alpine-based app build fails at apk.
#
# Idempotent and best-effort: safe on every boot (systemd) and every
# install/update. Never fails (a broken shim must not break installs;
# build failures still surface at the real apk step).
set -uo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"

# shellcheck disable=SC1090
if [ -f "$INSTALL_DIR/lib/egress_mirror.sh" ]; then
    source "$INSTALL_DIR/lib/egress_mirror.sh"
    ensure_egress_mirror
else
    echo "[egress-mirror] lib/egress_mirror.sh missing at $INSTALL_DIR — skipping" >&2
fi
