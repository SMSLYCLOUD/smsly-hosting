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
set -euo pipefail

COSIGN_BINARY=""
COSIGN_VERSION="v2.4.1"
# SHA-256 checksums for cosign v2.4.1 binaries (from official releases).
# Override with COSIGN_SHA256_<arch> env vars if needed.
COSIGN_SHA256_amd64="${COSIGN_SHA256_AMD64:-a7a4fd7b0ca22bb58e55e6569332c7851c434b4b4a39e00b88999be85a3f6e94}"
COSIGN_SHA256_arm64="${COSIGN_SHA256_ARM64:-7f29e8289e79a53a5e54c3a17b3a707b48b0a8b60e045f38d1c38f96d0c3e2e5}"

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
    curl -fsSL "$url" -o "$cosign_path" || {
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

cosign_verify_image() {
    local image="$1"
    if [ -z "$image" ]; then
        echo "[cosign] ERROR: no image specified"
        return 1
    fi

    _cosign_ensure_binary || return 1

    local cosign_keys="/opt/smsly-hosting/cosign-keys"
    local private_key="${COSIGN_PRIVATE_KEY_PATH:-${cosign_keys}/cosign.key}"
    local public_key="${cosign_keys}/cosign.pub"

    # ── Private-key verification (preferred for self-hosted nodes) ─────
    if [ -f "$public_key" ]; then
        echo "[cosign] Verifying $image (public key)..."
        if "$COSIGN_BINARY" verify --key "$public_key" "$image" ; then
            echo "[cosign] ✓ Signature verified (public key) for $image"
            return 0
        fi
    fi

    # ── Keyless Sigstore verification (CI-built images) ───────────────
    local repo_identity
    repo_identity="$(_cosign_get_repo)"
    local issuer="https://token.actions.githubusercontent.com"
    local identity="https://github.com/${repo_identity}/.github/workflows/deploy.yml@refs/heads/main"

    echo "[cosign] Verifying $image (keyless)..."
    if "$COSIGN_BINARY" verify \
        --certificate-oidc-issuer "$issuer" \
        --certificate-identity "$identity" \
        "$image" ; then
        echo "[cosign] ✓ Signature verified for $image"
        return 0
    fi

    local identity2="https://github.com/${repo_identity}/.github/workflows/cosign-sign.yml@refs/heads/main"
    if "$COSIGN_BINARY" verify \
        --certificate-oidc-issuer "$issuer" \
        --certificate-identity "$identity2" \
        "$image" ; then
        echo "[cosign] ✓ Signature verified for $image"
        return 0
    fi

    echo "[cosign] ERROR: Could not verify signature for $image"
    return 1
}
