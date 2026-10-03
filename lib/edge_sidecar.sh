#!/bin/bash
# lib/edge_sidecar.sh — build + install the smsly-edge-sidecar Go binary
# and its systemd unit. Sourced (not executed) by install.sh.
#
# Warn loudly, never abort the update (taste: installer never blocks).
# shellcheck disable=SC2317

EDGE_SIDECAR_SRC_DIR="${SMSLY_EDGE_SIDECAR_SRC_DIR:-$INSTALL_DIR/edge-sidecar}"
EDGE_SIDECAR_BIN="/usr/local/bin/smsly-edge-sidecar"
EDGE_SIDECAR_UNIT_SRC="$INSTALL_DIR/infrastructure/systemd/smsly-edge-sidecar.service"
EDGE_SIDECAR_UNIT_DST="/etc/systemd/system/smsly-edge-sidecar.service"
EDGE_SIDECAR_ENV_FILE="/etc/smsly/edge-sidecar.env"

_smsly_edge_sidecar_log() {
    local level="$1"; shift
    echo -e "${BLUE:-}  [edge-sidecar:$level] $*${NC:-}" || echo "  [edge-sidecar:$level] $*"
}

_smsly_edge_sidecar_warn() {
    echo -e "${YELLOW:-}  ⚠ edge-sidecar: $*${NC:-}" || echo "  ⚠ edge-sidecar: $*"
}

# Build the static binary. Prefers a pinned prebuilt artifact when the
# installer sets SMSLY_EDGE_SIDECAR_URL; otherwise builds with the local
# Go toolchain (installed best-effort).
smsly_edge_sidecar_build() {
    if [ -n "${SMSLY_EDGE_SIDECAR_URL:-}" ] && command -v curl >/dev/null 2>&1; then
        if curl -fsSL "$SMSLY_EDGE_SIDECAR_URL" -o "$EDGE_SIDECAR_BIN.tmp"; then
            chmod 0755 "$EDGE_SIDECAR_BIN.tmp" || true
            mv "$EDGE_SIDECAR_BIN.tmp" "$EDGE_SIDECAR_BIN" || {
                _smsly_edge_sidecar_warn "could not install prebuilt binary"
                return 0
            }
            _smsly_edge_sidecar_log info "installed prebuilt smsly-edge-sidecar"
            return 0
        fi
        _smsly_edge_sidecar_warn "prebuilt download failed, falling back to local build"
    fi
    if ! command -v go >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then
            (apt-get install -y golang-go 2>&1 | tail -2) || true
        fi
    fi
    if ! command -v go >/dev/null 2>&1; then
        _smsly_edge_sidecar_warn "Go toolchain unavailable — skipping build (edge keeps Django-backed ask/auth)"
        return 0
    fi
    if [ ! -f "$EDGE_SIDECAR_SRC_DIR/main.go" ]; then
        _smsly_edge_sidecar_warn "source missing at $EDGE_SIDECAR_SRC_DIR — skipping build"
        return 0
    fi
    if (cd "$EDGE_SIDECAR_SRC_DIR" && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$EDGE_SIDECAR_BIN.tmp" .) 2>&1 | tail -3; then
        chmod 0755 "$EDGE_SIDECAR_BIN.tmp" || true
        mv "$EDGE_SIDECAR_BIN.tmp" "$EDGE_SIDECAR_BIN" || {
            _smsly_edge_sidecar_warn "could not install built binary"
            return 0
        }
        _smsly_edge_sidecar_log info "built and installed smsly-edge-sidecar"
    else
        _smsly_edge_sidecar_warn "go build failed — edge keeps Django-backed ask/auth"
    fi
    return 0
}

# Write /etc/smsly/edge-sidecar.env from .env (secrets never on cmdline).
smsly_edge_sidecar_write_env() {
    local env_file="${INSTALL_DIR:-/opt/smsly-hosting}/.env"
    local edge_secret="" ask_secret=""
    if [ -f "$env_file" ]; then
        edge_secret="$(grep -E '^EDGE_JWT_SECRET=' "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')" || true
        ask_secret="$(grep -E '^CADDY_ASK_SECRET=' "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' )" || true
    fi
    mkdir -p /etc/smsly || true
    umask 027
    {
        echo "# Managed by smsly install — smsly-edge-sidecar env"
        if [ -n "$edge_secret" ]; then
            echo "EDGE_JWT_SECRET=$edge_secret"
        fi
        if [ -n "$ask_secret" ]; then
            echo "CADDY_ASK_SECRET=$ask_secret"
        fi
    } > "$EDGE_SIDECAR_ENV_FILE.tmp" || {
        _smsly_edge_sidecar_warn "could not write $EDGE_SIDECAR_ENV_FILE"
        return 0
    }
    chmod 0640 "$EDGE_SIDECAR_ENV_FILE.tmp" || true
    mv "$EDGE_SIDECAR_ENV_FILE.tmp" "$EDGE_SIDECAR_ENV_FILE" || {
        _smsly_edge_sidecar_warn "could not install $EDGE_SIDECAR_ENV_FILE"
        return 0
    }
    return 0
}

# Install + enable the systemd unit. Never fails the update.
smsly_edge_sidecar_install() {
    smsly_edge_sidecar_build || true
    smsly_edge_sidecar_write_env || true
    if [ ! -f "$EDGE_SIDECAR_UNIT_SRC" ]; then
        _smsly_edge_sidecar_warn "unit source missing ($EDGE_SIDECAR_UNIT_SRC) — skipping"
        return 0
    fi
    cp "$EDGE_SIDECAR_UNIT_SRC" "$EDGE_SIDECAR_UNIT_DST" 2>/dev/null || {
        _smsly_edge_sidecar_warn "could not install systemd unit"
        return 0
    }
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable smsly-edge-sidecar.service 2>/dev/null || \
        _smsly_edge_sidecar_warn "enable failed (non-fatal)"
    if [ -x "$EDGE_SIDECAR_BIN" ]; then
        systemctl restart smsly-edge-sidecar.service 2>/dev/null || \
            systemctl start smsly-edge-sidecar.service 2>/dev/null || \
            _smsly_edge_sidecar_warn "start failed (non-fatal)"
        _smsly_edge_sidecar_log info "smsly-edge-sidecar installed and started"
    else
        _smsly_edge_sidecar_warn "binary missing — unit installed but not started"
    fi
    return 0
}
