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

_EDGE_SIDECAR_BIN_CHANGED=0
_EDGE_SIDECAR_ENV_CHANGED=0

# Build the static binary. Prefers a pinned prebuilt artifact when the
# installer sets SMSLY_EDGE_SIDECAR_URL; otherwise builds with the local
# Go toolchain (installed best-effort).
smsly_edge_sidecar_build() {
    _EDGE_SIDECAR_BIN_CHANGED=0
    local build_dir="" tmp_bin="" build_log=""
    build_dir="$(mktemp -d /tmp/smsly-edge-sidecar-build.XXXXXX)"
    tmp_bin="${build_dir}/smsly-edge-sidecar"
    build_log="${build_dir}/build.log"

    if [ -n "${SMSLY_EDGE_SIDECAR_URL:-}" ] && command -v curl >/dev/null 2>&1; then
        if timeout 120 curl -fsSL --max-time 90 "$SMSLY_EDGE_SIDECAR_URL" -o "$tmp_bin"; then
            if [ ! -f "$EDGE_SIDECAR_BIN" ] || ! cmp -s "$tmp_bin" "$EDGE_SIDECAR_BIN"; then
                chmod 0755 "$tmp_bin" || true
                mv -f "$tmp_bin" "$EDGE_SIDECAR_BIN" || {
                    rm -rf "$build_dir"
                    _smsly_edge_sidecar_warn "could not install prebuilt binary"
                    return 0
                }
                _EDGE_SIDECAR_BIN_CHANGED=1
                _smsly_edge_sidecar_log info "installed prebuilt smsly-edge-sidecar"
            else
                _smsly_edge_sidecar_log info "smsly-edge-sidecar binary unchanged"
            fi
            rm -rf "$build_dir"
            return 0
        fi
        _smsly_edge_sidecar_warn "prebuilt download failed, falling back to local build"
    fi
    if [ ! -f "$EDGE_SIDECAR_SRC_DIR/main.go" ]; then
        rm -rf "$build_dir"
        _smsly_edge_sidecar_warn "source missing at $EDGE_SIDECAR_SRC_DIR — skipping build"
        return 0
    fi
    local go_bin="" go_ok=0 build_rc=1
    if command -v go >/dev/null 2>&1; then
        go_bin="$(command -v go)"
    elif [ -x "/usr/local/go/bin/go" ]; then
        go_bin="/usr/local/go/bin/go"
    fi
    if [ -n "$go_bin" ]; then
        local _go_ver="" _go_maj="" _go_min=""
        _go_ver="$("$go_bin" version 2>/dev/null | sed -n 's/.*go\([0-9]*\)\.\([0-9]*\).*/\1.\2/p' | head -n 1)"
        _go_maj="${_go_ver%%.*}"
        _go_min="${_go_ver#*.}"
        if [ -n "$_go_maj" ] && [ -n "$_go_min" ] && { [ "$_go_maj" -gt 1 ] || { [ "$_go_maj" -eq 1 ] && [ "$_go_min" -ge 22 ]; }; }; then
            go_ok=1
        fi
    fi
    if [ "$go_ok" -eq 1 ]; then
        build_rc=0
        (cd "$EDGE_SIDECAR_SRC_DIR" && timeout 180 env CGO_ENABLED=0 "$go_bin" build -trimpath -ldflags="-s -w" -o "$tmp_bin" .) >"$build_log" 2>&1 || build_rc=$?
    fi
    if [ "$build_rc" -ne 0 ] && command -v docker >/dev/null 2>&1 && timeout 10 docker info >/dev/null 2>&1; then
        local go_image="${EDGE_SIDECAR_GO_IMAGE:-golang:1.22}"
        _smsly_edge_sidecar_log info "Host Go unavailable or < 1.22; building smsly-edge-sidecar via ${go_image} container..."
        build_rc=0
        timeout 300 docker run --rm \
            -v "${EDGE_SIDECAR_SRC_DIR}:/src:ro" \
            -v "${build_dir}:/out" \
            -w /src \
            -e CGO_ENABLED=0 \
            "$go_image" \
            go build -trimpath -ldflags="-s -w" -o "/out/smsly-edge-sidecar" . >"$build_log" 2>&1 || build_rc=$?
    elif [ "$build_rc" -ne 0 ] && [ "$go_ok" -ne 1 ]; then
        rm -rf "$build_dir"
        _smsly_edge_sidecar_warn "Go >= 1.22 toolchain and Docker daemon unavailable — skipping build (edge keeps Django-backed ask/auth)"
        return 0
    fi
    if [ "$build_rc" -eq 0 ] && [ -s "$tmp_bin" ]; then
        if [ ! -f "$EDGE_SIDECAR_BIN" ] || ! cmp -s "$tmp_bin" "$EDGE_SIDECAR_BIN"; then
            chmod 0755 "$tmp_bin" || true
            mv -f "$tmp_bin" "$EDGE_SIDECAR_BIN" || {
                rm -rf "$build_dir"
                _smsly_edge_sidecar_warn "could not install built binary"
                return 0
            }
            _EDGE_SIDECAR_BIN_CHANGED=1
            _smsly_edge_sidecar_log info "built and installed smsly-edge-sidecar"
        else
            _smsly_edge_sidecar_log info "smsly-edge-sidecar binary unchanged"
        fi
    else
        tail -3 "$build_log" 2>/dev/null || true
        _smsly_edge_sidecar_warn "go build failed — edge keeps Django-backed ask/auth"
    fi
    rm -rf "$build_dir"
    return 0
}

# Write /etc/smsly/edge-sidecar.env from .env (secrets never on cmdline).
smsly_edge_sidecar_write_env() {
    _EDGE_SIDECAR_ENV_CHANGED=0
    local env_file="${INSTALL_DIR:-/opt/smsly-hosting}/.env"
    local edge_secret="" ask_secret=""
    if [ -f "$env_file" ]; then
        edge_secret="$(grep -E '^EDGE_JWT_SECRET=' "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')" || true
        ask_secret="$(grep -E '^CADDY_ASK_SECRET=' "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' )" || true
    fi
    if [ -z "$edge_secret" ]; then
        edge_secret="$(python3 -c "import secrets; print(secrets.token_hex(64))" 2>/dev/null || openssl rand -hex 64 2>/dev/null || true)"
        if [ -n "$edge_secret" ] && [ -f "$env_file" ]; then
            if command -v env_set_value >/dev/null 2>&1; then
                env_set_value "$env_file" "EDGE_JWT_SECRET" "$edge_secret" || true
            elif ! grep -q '^EDGE_JWT_SECRET=' "$env_file" 2>/dev/null; then
                printf 'EDGE_JWT_SECRET=%s\n' "$edge_secret" >> "$env_file" || true
            fi
        fi
    fi
    if [ -z "$ask_secret" ]; then
        ask_secret="$(python3 -c "import secrets; print(secrets.token_hex(64))" 2>/dev/null || openssl rand -hex 64 2>/dev/null || true)"
        if [ -n "$ask_secret" ] && [ -f "$env_file" ]; then
            if command -v env_set_value >/dev/null 2>&1; then
                env_set_value "$env_file" "CADDY_ASK_SECRET" "$ask_secret" || true
            elif ! grep -q '^CADDY_ASK_SECRET=' "$env_file" 2>/dev/null; then
                printf 'CADDY_ASK_SECRET=%s\n' "$ask_secret" >> "$env_file" || true
            fi
        fi
    fi
    mkdir -p /etc/smsly || true
    (
        umask 027
        {
            echo "# Managed by smsly install — smsly-edge-sidecar env"
            if [ -n "$edge_secret" ]; then
                echo "EDGE_JWT_SECRET=$edge_secret"
            fi
            if [ -n "$ask_secret" ]; then
                echo "CADDY_ASK_SECRET=$ask_secret"
            fi
        } > "$EDGE_SIDECAR_ENV_FILE.tmp"
    ) || {
        _smsly_edge_sidecar_warn "could not write $EDGE_SIDECAR_ENV_FILE"
        return 0
    }
    chmod 0640 "$EDGE_SIDECAR_ENV_FILE.tmp" || true
    if [ ! -f "$EDGE_SIDECAR_ENV_FILE" ] || ! cmp -s "$EDGE_SIDECAR_ENV_FILE.tmp" "$EDGE_SIDECAR_ENV_FILE"; then
        mv -f "$EDGE_SIDECAR_ENV_FILE.tmp" "$EDGE_SIDECAR_ENV_FILE" || {
            rm -f "$EDGE_SIDECAR_ENV_FILE.tmp" || true
            _smsly_edge_sidecar_warn "could not install $EDGE_SIDECAR_ENV_FILE"
            return 0
        }
        _EDGE_SIDECAR_ENV_CHANGED=1
    else
        rm -f "$EDGE_SIDECAR_ENV_FILE.tmp" || true
    fi
    return 0
}

# Install + enable the systemd unit. Never fails the update.
smsly_edge_sidecar_install() {
    local _unit_changed=0
    smsly_edge_sidecar_build || true
    smsly_edge_sidecar_write_env || true
    if [ ! -f "$EDGE_SIDECAR_UNIT_SRC" ]; then
        _smsly_edge_sidecar_warn "unit source missing ($EDGE_SIDECAR_UNIT_SRC) — skipping"
        return 0
    fi
    if [ ! -f "$EDGE_SIDECAR_UNIT_DST" ] || ! cmp -s "$EDGE_SIDECAR_UNIT_SRC" "$EDGE_SIDECAR_UNIT_DST"; then
        cp "$EDGE_SIDECAR_UNIT_SRC" "$EDGE_SIDECAR_UNIT_DST" 2>/dev/null || {
            _smsly_edge_sidecar_warn "could not install systemd unit"
            return 0
        }
        _unit_changed=1
        timeout 30 systemctl daemon-reload 2>/dev/null || true
    fi
    timeout 30 systemctl enable smsly-edge-sidecar.service 2>/dev/null || \
        _smsly_edge_sidecar_warn "enable failed (non-fatal)"
    if [ -x "$EDGE_SIDECAR_BIN" ]; then
        if [ "$_EDGE_SIDECAR_BIN_CHANGED" -eq 1 ] || [ "$_EDGE_SIDECAR_ENV_CHANGED" -eq 1 ] || [ "$_unit_changed" -eq 1 ] || ! systemctl is-active --quiet smsly-edge-sidecar.service 2>/dev/null; then
            timeout 60 systemctl restart smsly-edge-sidecar.service 2>/dev/null || \
                timeout 60 systemctl start smsly-edge-sidecar.service 2>/dev/null || \
                _smsly_edge_sidecar_warn "start failed (non-fatal)"
            _smsly_edge_sidecar_log info "smsly-edge-sidecar installed and started"
        else
            _smsly_edge_sidecar_log info "smsly-edge-sidecar unchanged and already running"
        fi
    else
        _smsly_edge_sidecar_warn "binary missing — unit installed but not started"
    fi
    return 0
}
