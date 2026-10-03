#!/bin/bash
# lib/napd.sh — build + install the smsly-napd Go binary and its unit.
# Sourced (not executed) by install/update flows.
#
# Warn loudly, never abort the update (taste: installer never blocks).
# shellcheck disable=SC2317

NAPD_SRC_DIR="${SMSLY_NAPD_SRC_DIR:-$INSTALL_DIR/napd}"
NAPD_BIN="/usr/local/bin/smsly-napd"
NAPD_UNIT_SRC="$INSTALL_DIR/infrastructure/systemd/smsly-napd.service"
NAPD_UNIT_DST="/etc/systemd/system/smsly-napd.service"
NAPD_ENV_FILE="/etc/smsly/napd.env"

_smsly_napd_log() {
    local level="$1"; shift
    echo -e "${BLUE:-}  [napd:$level] $*${NC:-}" || echo "  [napd:$level] $*"
}

_smsly_napd_warn() {
    echo -e "${YELLOW:-}  ⚠ napd: $*${NC:-}" || echo "  ⚠ napd: $*"
}

# Build the static binary. Prefers a pinned prebuilt artifact when the
# installer sets SMSLY_NAPD_URL; otherwise builds with the local Go
# toolchain (installed best-effort).
smsly_napd_build() {
    if [ -n "${SMSLY_NAPD_URL:-}" ] && command -v curl >/dev/null 2>&1; then
        if curl -fsSL "$SMSLY_NAPD_URL" -o "$NAPD_BIN.tmp"; then
            chmod 0755 "$NAPD_BIN.tmp" || true
            mv "$NAPD_BIN.tmp" "$NAPD_BIN" || {
                _smsly_napd_warn "could not install prebuilt binary"
                return 0
            }
            _smsly_napd_log info "installed prebuilt smsly-napd"
            return 0
        fi
        _smsly_napd_warn "prebuilt download failed, falling back to local build"
    fi
    if ! command -v go >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then
            (apt-get install -y golang-go 2>&1 | tail -2) || true
        fi
    fi
    if ! command -v go >/dev/null 2>&1; then
        _smsly_napd_warn "Go toolchain unavailable — skipping build (tier sleep/wake stays manual)"
        return 0
    fi
    if [ ! -f "$NAPD_SRC_DIR/main.go" ]; then
        _smsly_napd_warn "source missing at $NAPD_SRC_DIR — skipping build"
        return 0
    fi
    if (cd "$NAPD_SRC_DIR" && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$NAPD_BIN.tmp" .) 2>&1 | tail -3; then
        chmod 0755 "$NAPD_BIN.tmp" || true
        mv "$NAPD_BIN.tmp" "$NAPD_BIN" || {
            _smsly_napd_warn "could not install built binary"
            return 0
        }
        _smsly_napd_log info "built and installed smsly-napd"
    else
        _smsly_napd_warn "go build failed — tier sleep/wake stays manual"
    fi
    return 0
}

# Ensure NAPD_SHARED_SECRET exists in .env (generating once when absent)
# and write /etc/smsly/napd.env for the unit. Backend/workers read the
# same value from .env via env_file — no restart needed here because the
# update flow recreates backend containers after this runs.
smsly_napd_write_env() {
    local env_file="${INSTALL_DIR:-/opt/smsly-hosting}/.env"
    local napd_secret=""
    if [ -f "$env_file" ]; then
        napd_secret="$(grep -E '^NAPD_SHARED_SECRET=' "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')" || true
    fi
    if [ -z "$napd_secret" ]; then
        if command -v openssl >/dev/null 2>&1; then
            napd_secret="$(openssl rand -hex 32 2>/dev/null)" || true
        fi
        if [ -z "$napd_secret" ]; then
            napd_secret="$(head -c 32 /dev/urandom 2>/dev/null | od -An -tx1 | tr -d ' \n')" || true
        fi
        if [ -n "$napd_secret" ] && [ -f "$env_file" ]; then
            printf '\nNAPD_SHARED_SECRET=%s\n' "$napd_secret" >> "$env_file" || {
                _smsly_napd_warn "could not append NAPD_SHARED_SECRET to .env"
                napd_secret=""
            }
        fi
    fi
    mkdir -p /etc/smsly || true
    umask 027
    {
        echo "# Managed by smsly install — smsly-napd env"
        if [ -n "$napd_secret" ]; then
            echo "NAPD_SHARED_SECRET=$napd_secret"
        fi
    } > "$NAPD_ENV_FILE.tmp" || {
        _smsly_napd_warn "could not write $NAPD_ENV_FILE"
        return 0
    }
    chmod 0640 "$NAPD_ENV_FILE.tmp" || true
    mv "$NAPD_ENV_FILE.tmp" "$NAPD_ENV_FILE" || {
        _smsly_napd_warn "could not install $NAPD_ENV_FILE"
        return 0
    }
    return 0
}

# Install + enable the systemd unit. Never fails the update.
smsly_napd_install() {
    smsly_napd_build || true
    smsly_napd_write_env || true
    if [ ! -f "$NAPD_UNIT_SRC" ]; then
        _smsly_napd_warn "unit source missing ($NAPD_UNIT_SRC) — skipping"
        return 0
    fi
    cp "$NAPD_UNIT_SRC" "$NAPD_UNIT_DST" 2>/dev/null || {
        _smsly_napd_warn "could not install systemd unit"
        return 0
    }
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable smsly-napd.service 2>/dev/null || \
        _smsly_napd_warn "enable failed (non-fatal)"
    if [ -x "$NAPD_BIN" ]; then
        systemctl restart smsly-napd.service 2>/dev/null || \
            systemctl start smsly-napd.service 2>/dev/null || \
            _smsly_napd_warn "start failed (non-fatal)"
        _smsly_napd_log info "smsly-napd installed and started"
    else
        _smsly_napd_warn "binary missing — unit installed but not started"
    fi
    return 0
}
