#!/usr/bin/env bash
# lib/cryptd.sh - Build and install the opt-in Trulay Grid Go cryptographic controller (grid-cryptd).
# Follows the napd.sh pattern: installs the binary + unit + default opt-in env safely without
# aborting updates or enabling enforcement until the operator opts in (`CRYPTD_ENABLED=true`).

smsly_cryptd_install() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local src_dir="$install_dir/cryptd"
    local unit_src="$install_dir/infrastructure/systemd/smsly-cryptd.service"
    local vault_unit_src="$install_dir/infrastructure/systemd/smsly-root-vault.service"
    local bin_dst="/usr/local/bin/grid-cryptd"
    local env_dir="/etc/smsly"
    local env_file="$env_dir/cryptd.env"
    local systemd_dst="/etc/systemd/system/smsly-cryptd.service"

    if [ ! -d "$src_dir" ]; then
        return 0
    fi

    local go_bin=""
    if command -v go >/dev/null 2>&1; then
        go_bin="$(command -v go)"
    elif [ -x "/usr/local/go/bin/go" ]; then
        go_bin="/usr/local/go/bin/go"
    fi

    mkdir -p "$env_dir" /var/lib/smsly/keystore /var/lib/smsly/encrypted-volumes
    chmod 0700 /var/lib/smsly/keystore /var/lib/smsly/encrypted-volumes 2>/dev/null || true

    # Build static binary atomically; replace only when bytes actually change (AGENTS.md #4).
    local build_dir=""
    build_dir="$(mktemp -d /tmp/grid-cryptd-build.XXXXXX)"
    local tmp_bin="${build_dir}/grid-cryptd"
    local build_ok=0

    if [ -n "$go_bin" ]; then
        if (cd "$src_dir" && timeout 180 env CGO_ENABLED=0 "$go_bin" build -trimpath -ldflags="-s -w" -o "$tmp_bin" ./cmd/grid-cryptd); then
            build_ok=1
        fi
    elif command -v docker >/dev/null 2>&1 && timeout 10 docker info >/dev/null 2>&1; then
        local go_image="${CRYPTD_GO_IMAGE:-golang:1.22}"
        echo "[cryptd] Host Go not found; building grid-cryptd via ${go_image} container..."
        if timeout 300 docker run --rm \
            -v "${src_dir}:/src:ro" \
            -v "${build_dir}:/out" \
            -w /src \
            -e CGO_ENABLED=0 \
            "$go_image" \
            go build -trimpath -ldflags="-s -w" -o "/out/grid-cryptd" ./cmd/grid-cryptd; then
            build_ok=1
        fi
    else
        rm -rf "$build_dir"
        echo "[cryptd] Neither Go toolchain nor Docker daemon available; skipping grid-cryptd build." >&2
        return 0
    fi

    if [ "$build_ok" -ne 1 ] || [ ! -s "$tmp_bin" ]; then
        rm -rf "$build_dir"
        echo "[cryptd] WARN: go build grid-cryptd failed (non-fatal)." >&2
        return 0
    fi

    local bin_changed=0
    if [ ! -f "$bin_dst" ] || ! cmp -s "$tmp_bin" "$bin_dst"; then
        chmod 0755 "$tmp_bin"
        mv -f "$tmp_bin" "$bin_dst"
        bin_changed=1
    fi
    rm -rf "$build_dir"

    # Sync shared secret & opt-in flag between INSTALL_DIR/.env (backend/celery containers)
    # and /etc/smsly/cryptd.env (host systemd unit), mirroring lib/napd.sh.
    local root_env="$install_dir/.env"
    local shared_sec="${CRYPTD_SHARED_SECRET:-}"
    if [ -z "$shared_sec" ] && [ -f "$root_env" ]; then
        shared_sec="$(grep -E '^CRYPTD_SHARED_SECRET=' "$root_env" | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' || true)"
    fi
    if [ -z "$shared_sec" ] && [ -f "$env_file" ]; then
        shared_sec="$(grep -E '^CRYPTD_SHARED_SECRET=' "$env_file" | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' || true)"
    fi
    if [ -z "$shared_sec" ]; then
        if command -v openssl >/dev/null 2>&1; then
            shared_sec="$(openssl rand -hex 32)"
        else
            shared_sec="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        fi
    fi
    if [ -f "$root_env" ] && ! grep -qE '^CRYPTD_SHARED_SECRET=' "$root_env"; then
        printf '\nCRYPTD_SHARED_SECRET=%s\n' "$shared_sec" >> "$root_env" || true
    fi

    # Seed /etc/smsly/cryptd.env once if missing.
    # Bind 0.0.0.0:9105 so Docker containers (backend/celery) reach host systemd via host.docker.internal
    # (port stays host-local / blocked in UFW; every endpoint except /health requires X-Cryptd-Secret).
    if [ ! -f "$env_file" ]; then
        {
            echo "# Trulay Grid Cryptographic Controller (opt-in: set CRYPTD_ENABLED=true and configure external vault)"
            echo "CRYPTD_ENABLED=${CRYPTD_ENABLED:-false}"
            echo "CRYPTD_LISTEN_ADDR=0.0.0.0:9105"
            echo "CRYPTD_SHARED_SECRET=$shared_sec"
            echo "CRYPTD_VAULT_PROVIDER=${CRYPTD_VAULT_PROVIDER:-http_vault}"
            echo "CRYPTD_VAULT_ENDPOINT=${CRYPTD_VAULT_ENDPOINT:-}"
            echo "CRYPTD_EPOCH_DURATION=168h"
            echo "CRYPTD_GRACE_DURATION=24h"
            echo "CRYPTD_KEYSTORE_DIR=/var/lib/smsly/keystore"
            echo "SMSLY_ENCRYPTED_VOLUME_DIR=/var/lib/smsly/encrypted-volumes"
            echo "SMSLY_VOLUME_CRYPT_SCRIPT=$install_dir/scripts/smsly-volume-crypt.sh"
            echo "# Optional PG/Redis wire gate (bind 0.0.0.0 behind UFW when Docker containers connect via host.docker.internal):"
            echo "# CRYPTD_PG_LISTEN=0.0.0.0:15432"
            echo "# CRYPTD_PG_UPSTREAM=127.0.0.1:6432"
            echo "# CRYPTD_REDIS_LISTEN=0.0.0.0:16379"
            echo "# CRYPTD_REDIS_UPSTREAM=127.0.0.1:6379"
        } > "$env_file"
        chmod 0600 "$env_file"
    fi

    local unit_changed=0
    if [ -f "$unit_src" ]; then
        if [ ! -f "$systemd_dst" ] || ! cmp -s "$unit_src" "$systemd_dst"; then
            cp "$unit_src" "$systemd_dst"
            unit_changed=1
        fi
    fi
    if [ -f "$vault_unit_src" ]; then
        cp "$vault_unit_src" /etc/systemd/system/smsly-root-vault.service 2>/dev/null || true
    fi

    if command -v systemctl >/dev/null 2>&1; then
        if [ "$unit_changed" -eq 1 ]; then
            timeout 30 systemctl daemon-reload || true
        fi

        # Opt-in guard (user chooses to enable CRYPTD_ENABLED=true or not).
        local enabled_flag="false"
        if [ -f "$env_file" ]; then
            enabled_flag="$(grep -E '^CRYPTD_ENABLED=' "$env_file" | tail -n 1 | cut -d= -f2- | tr -d '"' | tr '[:upper:]' '[:lower:]' || echo false)"
        fi
        if [ "${CRYPTD_ENABLED:-$enabled_flag}" = "true" ]; then
            if [ -f "$root_env" ] && ! grep -qE '^SMSLY_CRYPTD_URL=' "$root_env"; then
                printf 'SMSLY_CRYPTD_URL=http://host.docker.internal:9105\n' >> "$root_env" || true
            fi
            timeout 30 systemctl enable smsly-cryptd >/dev/null 2>&1 || true
            if [ "$bin_changed" -eq 1 ] || [ "$unit_changed" -eq 1 ] || ! systemctl is-active --quiet smsly-cryptd; then
                timeout 60 systemctl restart smsly-cryptd || echo "[cryptd] WARN: smsly-cryptd failed to start (check external vault config)" >&2
            fi
        fi
    fi
}
