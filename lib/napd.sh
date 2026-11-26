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

_NAPD_BIN_CHANGED=0
_NAPD_ENV_CHANGED=0

# Build the static binary. Prefers a pinned prebuilt artifact when the
# installer sets SMSLY_NAPD_URL; otherwise builds with the local Go
# toolchain (installed best-effort).
smsly_napd_build() {
    _NAPD_BIN_CHANGED=0
    local build_dir="" tmp_bin="" build_log=""
    build_dir="$(mktemp -d /tmp/smsly-napd-build.XXXXXX)"
    tmp_bin="${build_dir}/smsly-napd"
    build_log="${build_dir}/build.log"

    if [ -n "${SMSLY_NAPD_URL:-}" ] && command -v curl >/dev/null 2>&1; then
        if timeout 120 curl -fsSL --max-time 90 "$SMSLY_NAPD_URL" -o "$tmp_bin"; then
            if [ ! -f "$NAPD_BIN" ] || ! cmp -s "$tmp_bin" "$NAPD_BIN"; then
                chmod 0755 "$tmp_bin" || true
                mv -f "$tmp_bin" "$NAPD_BIN" || {
                    rm -rf "$build_dir"
                    _smsly_napd_warn "could not install prebuilt binary"
                    return 0
                }
                _NAPD_BIN_CHANGED=1
                _smsly_napd_log info "installed prebuilt smsly-napd"
            else
                _smsly_napd_log info "smsly-napd binary unchanged"
            fi
            rm -rf "$build_dir"
            return 0
        fi
        _smsly_napd_warn "prebuilt download failed, falling back to local build"
    fi
    if [ ! -f "$NAPD_SRC_DIR/main.go" ]; then
        rm -rf "$build_dir"
        _smsly_napd_warn "source missing at $NAPD_SRC_DIR — skipping build"
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
        (cd "$NAPD_SRC_DIR" && timeout 180 env CGO_ENABLED=0 "$go_bin" build -trimpath -ldflags="-s -w" -o "$tmp_bin" .) >"$build_log" 2>&1 || build_rc=$?
    fi
    if [ "$build_rc" -ne 0 ] && command -v docker >/dev/null 2>&1 && timeout 10 docker info >/dev/null 2>&1; then
        local go_image="${NAPD_GO_IMAGE:-golang:1.22}"
        _smsly_napd_log info "Host Go unavailable or < 1.22; building smsly-napd via ${go_image} container..."
        build_rc=0
        timeout 300 docker run --rm \
            -v "${NAPD_SRC_DIR}:/src:ro" \
            -v "${build_dir}:/out" \
            -w /src \
            -e CGO_ENABLED=0 \
            "$go_image" \
            go build -trimpath -ldflags="-s -w" -o "/out/smsly-napd" . >"$build_log" 2>&1 || build_rc=$?
    elif [ "$build_rc" -ne 0 ] && [ "$go_ok" -ne 1 ]; then
        rm -rf "$build_dir"
        _smsly_napd_warn "Go >= 1.22 toolchain and Docker daemon unavailable — skipping build (tier sleep/wake stays manual)"
        return 0
    fi
    if [ "$build_rc" -eq 0 ] && [ -s "$tmp_bin" ]; then
        if [ ! -f "$NAPD_BIN" ] || ! cmp -s "$tmp_bin" "$NAPD_BIN"; then
            chmod 0755 "$tmp_bin" || true
            mv -f "$tmp_bin" "$NAPD_BIN" || {
                rm -rf "$build_dir"
                _smsly_napd_warn "could not install built binary"
                return 0
            }
            _NAPD_BIN_CHANGED=1
            _smsly_napd_log info "built and installed smsly-napd"
        else
            _smsly_napd_log info "smsly-napd binary unchanged"
        fi
    else
        tail -3 "$build_log" 2>/dev/null || true
        _smsly_napd_warn "go build failed — tier sleep/wake stays manual"
    fi
    rm -rf "$build_dir"
    return 0
}

# Ensure NAPD_SHARED_SECRET exists in .env (generating once when absent)
# and write /etc/smsly/napd.env for the unit. Backend/workers read the
# same value from .env via env_file — no restart needed here because the
# update flow recreates backend containers after this runs.
smsly_napd_write_env() {
    _NAPD_ENV_CHANGED=0
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
    (
        umask 027
        {
            echo "# Managed by smsly install — smsly-napd env"
            if [ -n "$napd_secret" ]; then
                echo "NAPD_SHARED_SECRET=$napd_secret"
            fi
        } > "$NAPD_ENV_FILE.tmp"
    ) || {
        _smsly_napd_warn "could not write $NAPD_ENV_FILE"
        return 0
    }
    chmod 0640 "$NAPD_ENV_FILE.tmp" || true
    if [ ! -f "$NAPD_ENV_FILE" ] || ! cmp -s "$NAPD_ENV_FILE.tmp" "$NAPD_ENV_FILE"; then
        mv -f "$NAPD_ENV_FILE.tmp" "$NAPD_ENV_FILE" || {
            rm -f "$NAPD_ENV_FILE.tmp" || true
            _smsly_napd_warn "could not install $NAPD_ENV_FILE"
            return 0
        }
        _NAPD_ENV_CHANGED=1
    else
        rm -f "$NAPD_ENV_FILE.tmp" || true
    fi
    return 0
}

# Install + enable the systemd unit. Never fails the update.
smsly_napd_install() {
    local _unit_changed=0
    smsly_napd_build || true
    smsly_napd_write_env || true
    if [ ! -f "$NAPD_UNIT_SRC" ]; then
        _smsly_napd_warn "unit source missing ($NAPD_UNIT_SRC) — skipping"
        return 0
    fi
    if [ ! -f "$NAPD_UNIT_DST" ] || ! cmp -s "$NAPD_UNIT_SRC" "$NAPD_UNIT_DST"; then
        cp "$NAPD_UNIT_SRC" "$NAPD_UNIT_DST" 2>/dev/null || {
            _smsly_napd_warn "could not install systemd unit"
            return 0
        }
        _unit_changed=1
        timeout 30 systemctl daemon-reload 2>/dev/null || true
    fi
    timeout 30 systemctl enable smsly-napd.service 2>/dev/null || \
        _smsly_napd_warn "enable failed (non-fatal)"
    if [ -x "$NAPD_BIN" ]; then
        if [ "$_NAPD_BIN_CHANGED" -eq 1 ] || [ "$_NAPD_ENV_CHANGED" -eq 1 ] || [ "$_unit_changed" -eq 1 ] || ! systemctl is-active --quiet smsly-napd.service 2>/dev/null; then
            timeout 60 systemctl restart smsly-napd.service 2>/dev/null || \
                timeout 60 systemctl start smsly-napd.service 2>/dev/null || \
                _smsly_napd_warn "start failed (non-fatal)"
            _smsly_napd_log info "smsly-napd installed and started"
        else
            _smsly_napd_log info "smsly-napd unchanged and already running"
        fi
    else
        _smsly_napd_warn "binary missing — unit installed but not started"
    fi
    return 0
}
