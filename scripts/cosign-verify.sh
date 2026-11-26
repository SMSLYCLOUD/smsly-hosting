#!/bin/bash
# ============================================================
# SMSLY Hosting — Cosign Image Verification
# Verifies that an image pulled from the registry carries a
# valid Cosign signature from the platform CI pipeline.
#
# Usage:
#   source scripts/cosign-verify.sh
#   cosign_verify_image "registry:5000/smsly/backend:latest"
# ============================================================
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    set -euo pipefail
fi

COSIGN_BINARY=""
COSIGN_VERSION="v2.4.1"
# SHA-256 checksums for cosign v2.4.1 binaries (from official releases).
# Override with COSIGN_SHA256_<arch> env vars if needed.
COSIGN_SHA256_amd64="${COSIGN_SHA256_AMD64:-8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b}"
COSIGN_SHA256_arm64="${COSIGN_SHA256_ARM64:-3b2e2e3854d0356c45fe6607047526ccd04742d20bd44afb5be91fa2a6e7cb4a}"

_cosign_ensure_binary() {
    if command -v cosign ; then
        COSIGN_BINARY="cosign"
        return 0
    fi
    local cosign_path="/usr/local/bin/cosign"
    if [ -x "$cosign_path" ]; then
        COSIGN_BINARY="$cosign_path"
        return 0
    fi
    echo "[cosign] Installing cosign ${COSIGN_VERSION}..."
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="amd64" ;;
        aarch64) arch="arm64" ;;
        *)       echo "[cosign] ERROR: unsupported architecture: $arch"; return 1 ;;
    esac
    local url="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${arch}"
    curl -fsSL --connect-timeout 15 --max-time 60 "$url" -o "$cosign_path" || {
        echo "[cosign] ERROR: failed to download cosign"
        return 1
    }
    # Verify checksum
    local expected_sha_var="COSIGN_SHA256_${arch}"
    local expected_sha="${!expected_sha_var}"
    if [ -n "$expected_sha" ]; then
        local actual_sha
        actual_sha="$(sha256sum "$cosign_path" | awk '{print $1}')"
        if [ "$actual_sha" != "$expected_sha" ]; then
            echo "[cosign] ERROR: checksum mismatch for cosign ${COSIGN_VERSION}"
            echo "[cosign]   expected: $expected_sha"
            echo "[cosign]   actual:   $actual_sha"
            rm -f "$cosign_path"
            return 1
        fi
        echo "[cosign] Checksum verified"
    else
        echo "[cosign] WARNING: no checksum configured for arch $arch — skipping verification"
    fi
    chmod +x "$cosign_path"
    "$cosign_path" version  || {
        echo "[cosign] ERROR: downloaded binary failed version check"
        rm -f "$cosign_path"
        return 1
    }
    COSIGN_BINARY="$cosign_path"
    echo "[cosign] Installed cosign ${COSIGN_VERSION}"
}

_cosign_get_repo() {
    local repo_url
    repo_url="$(git -C /opt/smsly-hosting remote get-url origin  || echo "")"
    if [ -z "$repo_url" ]; then
        repo_url="https://github.com/SMSLYCLOUD/smsly-hosting"
    fi
    echo "$repo_url" | sed 's|^https://github.com/||' | sed 's|\.git$||'
}

COSIGN_IMAGE=""

# Runs one `cosign verify`, capturing output.
# Returns 0 = verified, 2 = skipped (registry auth required: a private
# image with no `docker login` cannot be verified — non-fatal),
# 1 = genuine verification failure (output already echoed).
_cosign_try_verify() {
    local _out=""
    local _rc=0
    if command -v timeout >/dev/null 2>&1; then
        _out="$(timeout 60 "$COSIGN_BINARY" verify "$@" 2>&1)" || _rc=$?
    else
        _out="$("$COSIGN_BINARY" verify "$@" 2>&1)" || _rc=$?
    fi
    if [ "$_rc" -eq 0 ]; then
        echo "$_out"
        return 0
    fi
    if echo "$_out" | grep -qi "UNAUTHORIZED\|authentication required"; then
        if [ "${COSIGN_ALLOW_UNAUTH_SKIP:-false}" = "true" ]; then
            echo "[cosign] SKIP: $COSIGN_IMAGE requires registry auth and COSIGN_ALLOW_UNAUTH_SKIP=true — signature check skipped"
            return 2
        fi
        echo "[cosign] ERROR: $COSIGN_IMAGE requires registry auth — failing closed (set COSIGN_ALLOW_UNAUTH_SKIP=true to override)"
        echo "$_out"
        return 1
    fi
    echo "$_out"
    return 1
}

cosign_verify_image() {
    local image="$1"
    if [ -z "$image" ]; then
        echo "[cosign] ERROR: no image specified"
        return 1
    fi

    _cosign_ensure_binary || return 1
    COSIGN_IMAGE="$image"

    local cosign_keys="/opt/smsly-hosting/cosign-keys"
    local private_key="${COSIGN_PRIVATE_KEY_PATH:-${cosign_keys}/cosign.key}"
    local public_key="${cosign_keys}/cosign.pub"

    # ── Private-key verification (preferred for self-hosted nodes) ─────
    if [ -f "$public_key" ]; then
        echo "[cosign] Verifying $image (public key)..."
        local _rc1=""
        _cosign_try_verify --key "$public_key" "$image" && { echo "[cosign] ✓ Signature verified (public key) for $image"; return 0; }
        _rc1=$?
        if [ "$_rc1" -eq 2 ]; then
            return 0
        fi
    fi

    # ── Keyless Sigstore verification (CI-built images) ───────────────
    local repo_identity="" repo_identity_esc=""
    repo_identity="$(_cosign_get_repo)"
    repo_identity_esc="${repo_identity//./\\.}"
    local issuer="https://token.actions.githubusercontent.com"
    local identity_re="^https://github\\.com/${repo_identity_esc}/\\.github/workflows/(deploy|deploy-helm|cosign-sign)\\.yml@refs/heads/master$"

    echo "[cosign] Verifying $image (keyless)..."
    local _rc2=""
    _cosign_try_verify \
        --certificate-oidc-issuer "$issuer" \
        --certificate-identity-regexp "$identity_re" \
        "$image" && { echo "[cosign] ✓ Signature verified for $image"; return 0; }
    _rc2=$?
    if [ "$_rc2" -eq 2 ]; then
        return 0
    fi

    echo "[cosign] ERROR: Could not verify signature for $image"
    return 1
}
